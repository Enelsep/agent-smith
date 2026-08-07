from __future__ import annotations

import asyncio
import contextlib
import threading
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from agent_smith.mcp.formatter import to_sandbox_manual
from agent_smith.mcp.registry import MCPToolRegistry
from agent_smith.mcp.sandbox_integration import execute_mcp_tool_call

if TYPE_CHECKING:
    from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition

DEFAULT_STARTUP_TIMEOUT = 30.0
"""How long to wait for the server to come up and list its tools. Generous:
a stdio server is a process that has to start and import its dependencies."""

SHUTDOWN_TIMEOUT = 5.0
"""How long to wait for the session thread to unwind. It is a daemon, so a
server that refuses to close cannot keep the CLI from exiting."""

_Request = tuple[str, "dict[str, Any]", "Future[str]"]


class MCPBridgeError(RuntimeError):
    """Raised when the server could not be reached or refused to list tools."""


class MCPBridge:
    """One MCP session, owned by one background task, called from anywhere."""

    def __init__(
        self,
        client: MCPClientProtocol,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ) -> None:
        self._client = client
        self._startup_timeout = startup_timeout
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._requests: asyncio.Queue[_Request | None] | None = None
        self._failure: BaseException | None = None
        self.tool_defs: list[MCPToolDefinition] = []
        self.manual: str = to_sandbox_manual([])

    def start(self) -> None:
        """Connect, discover the tools, and leave the session running.

        Raises `MCPBridgeError` if the server never became usable, so that the
        CLI can say so and stop instead of opening a prompt whose tools would
        all fail one at a time.
        """
        thread = threading.Thread(
            target=self._serve_forever, name="mcp-bridge", daemon=True
        )
        self._thread = thread
        thread.start()

        if not self._ready.wait(self._startup_timeout):
            self.close()
            raise MCPBridgeError(
                f"the MCP server did not answer within {self._startup_timeout:.0f}s"
            )
        if self._failure is not None:
            failure = self._failure
            self.close()
            raise MCPBridgeError(f"cannot use the MCP server: {failure}") from failure

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Run one tool and block until it answers. Never raises.

        This is what the sandbox's tool stubs ultimately reach, so a failure
        has to come back as text the caller can read: it becomes the tool's
        output, and for the model that is an observation like any other.
        """
        loop, requests = self._loop, self._requests
        if loop is None or requests is None:
            return (
                f"Observation: no MCP session is running, so the tool "
                f"'{name}' is not available."
            )

        answer: Future[str] = Future()
        try:
            loop.call_soon_threadsafe(requests.put_nowait, (name, arguments, answer))
        except RuntimeError:
            # The loop closed between the check above and here.
            return f"Observation: the MCP session ended before '{name}' could run."

        try:
            return answer.result(timeout=600.0)
        except Exception as failed:  # noqa: BLE001 - a broken tool is an observation
            return f"Observation: tool '{name}' failed: {failed}"

    def close(self) -> None:
        """Ask the session to disconnect, and wait briefly for it to finish."""
        loop, requests, thread = self._loop, self._requests, self._thread
        if loop is not None and requests is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(requests.put_nowait, None)
        if thread is not None:
            thread.join(SHUTDOWN_TIMEOUT)
        self._thread, self._loop, self._requests = None, None, None

    def _serve_forever(self) -> None:
        """The thread body: one event loop, for one session, start to finish."""
        try:
            asyncio.run(self._serve())
        except Exception as unexpected:  # noqa: BLE001 - reported through start()
            self._failure = unexpected
        finally:
            self._ready.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        requests: asyncio.Queue[_Request | None] = asyncio.Queue()
        self._requests = requests

        registry = MCPToolRegistry(self._client)
        try:
            await self._client.connect()
            await registry.discover_tools()
        except Exception as unreachable:  # noqa: BLE001 - reported through start()
            self._failure = unreachable
            await self._disconnect()
            return

        self.tool_defs = registry.get_tool_definitions()
        self.manual = to_sandbox_manual(self.tool_defs)
        self._ready.set()

        try:
            while True:
                request = await requests.get()
                if request is None:
                    return
                name, arguments, answer = request
                if answer.set_running_or_notify_cancel():
                    answer.set_result(
                        await execute_mcp_tool_call(registry, name, arguments)
                    )
        finally:
            await self._disconnect()

    async def _disconnect(self) -> None:
        """Close the transport, tolerating a server that has already gone."""
        with contextlib.suppress(Exception):
            await self._client.disconnect()
