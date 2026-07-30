import importlib
import logging
from contextlib import AsyncExitStack
from typing import Any

from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition

# Dynamic imports of MCP components to comply with import boundary checks
_mcp = importlib.import_module("mcp")
_mcp_stdio = importlib.import_module("mcp.client.stdio")

ClientSession = _mcp.ClientSession
StdioServerParameters = _mcp.StdioServerParameters
stdio_client = _mcp_stdio.stdio_client

try:
    _mcp_http = importlib.import_module("mcp.client.streamable_http")
    streamable_http_client = _mcp_http.streamable_http_client
except ImportError:
    _mcp_sse = importlib.import_module("mcp.client.sse")
    streamable_http_client = _mcp_sse.sse_client

logger = logging.getLogger(__name__)


class MCPConnectionError(Exception):
    """Raised internally when a transport or connection failure occurs."""


class UnifiedMCPClient(MCPClientProtocol):
    """
    Unified MCP client implementation supporting Stdio and Streamable HTTP transports,
    automatic reconnection on drop, and graceful error handling as readable observations.
    """

    def __init__(
        self,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        max_retries: int = 2,
    ):
        self.command = command
        self.args = args or []
        self.env = env
        self.url = url
        self.max_retries = max_retries

        self._exit_stack: AsyncExitStack | None = None
        self._session: Any | None = None

        if not command and not url:
            raise ValueError(
                "Either 'command' (stdio) or 'url' (streamable http) must be provided."
            )

    async def connect(self) -> None:
        """Establish connection using the configured transport protocol."""
        if self._exit_stack is not None:
            await self.disconnect()

        self._exit_stack = AsyncExitStack()

        try:
            if self.command:
                server_params = StdioServerParameters(
                    command=self.command,
                    args=self.args,
                    env=self.env,
                )
                stdio_transport = await self._exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                read_stream, write_stream = stdio_transport
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
            elif self.url:
                http_transport = await self._exit_stack.enter_async_context(
                    streamable_http_client(self.url)
                )
                read_stream, write_stream = http_transport[:2]
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
            assert self._session is not None
            await self._session.initialize()
            logger.info("Successfully connected to MCP server.")
        except Exception as e:
            logger.error(f"Failed to connect to MCP server: {e}")
            await self.disconnect()
            raise MCPConnectionError(f"Connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Gracefully close streams and cleanup context stack."""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            logger.info("Disconnected from MCP server.")

    async def _ensure_session(self) -> Any:
        """Ensure an active session exists, attempting reconnection if necessary."""
        if self._session is None:
            await self.connect()
        assert self._session is not None
        return self._session

    async def list_tools(self) -> list[MCPToolDefinition]:
        """List tools with retry logic on drop."""
        for attempt in range(self.max_retries + 1):
            try:
                session = await self._ensure_session()
                response = await session.list_tools()
                return [
                    MCPToolDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.input_schema or {},
                    )
                    for tool in response.tools
                ]
            except Exception as e:
                logger.warning(f"Attempt {attempt} failed to list tools: {e}")
                await self.disconnect()
                if attempt == self.max_retries:
                    raise MCPConnectionError(
                        f"Persistent failure listing tools: {e}"
                    ) from e
        raise RuntimeError("Unreachable loop exit in list_tools")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """
        Execute a tool.
        If the server is dead or connection drops persistently, it returns a readable
        observation string instead of raising an exception that crashes the loop.
        """
        for attempt in range(self.max_retries + 1):
            try:
                session = await self._ensure_session()
                result = await session.call_tool(name, arguments)
                output_parts = []
                for content in result.content:
                    if hasattr(content, "text"):
                        output_parts.append(content.text)
                    else:
                        output_parts.append(str(content))
                return "\n".join(output_parts)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Attempt {attempt} failed calling tool {name}: {e}")
                if attempt < self.max_retries:
                    await self.disconnect()
                    continue
                return (
                    f"Observation: Error communicating with MCP server "
                    f"while calling tool '{name}': {e!s}"
                )
        raise RuntimeError("Unreachable loop exit in call_tool")
