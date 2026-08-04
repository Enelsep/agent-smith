from __future__ import annotations

import contextlib
import multiprocessing as mp
import time
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import Self

from .protocol import ExecRequest, ExecResult, Outcome, ToolCall, ToolReply
from .worker import worker_main

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from multiprocessing.connection import Connection
    from types import TracebackType

    from agent_smith.mcp.protocol import MCPToolDefinition
    from agent_smith.models.contract import SandboxConfig

    ParentConn = Connection[ExecRequest |
                            ToolReply | None, ExecResult | ToolCall]

    ToolHandler = Callable[[str, dict[str, object]], str]

HARD_TIMEOUT_MARGIN = 5.0


class Sandbox:
    """A restartable child process holding a persistent execution namespace."""

    DEFAULT_IMPORTS: ClassVar[list[str]] = [
        "math",
        "math.*",
        "collections",
        "collections.*",
        "itertools",
        "re",
        "json",
        "typing",
        "typing.*",
        "functools",
        "operator",
        "heapq",
        "bisect",
        "copy",
        "string",
        "random",
        "datetime",
        "datetime.*",
        "array",
        "cmath",
    ]

    DEFAULT_DIRECTORIES: ClassVar[list[str]] = ["/testbed", "/tmp/agent"]

    def __init__(
        self,
        timeout: float = 30.0,
        authorized_imports: Iterable[str] | None = None,
        allowed_directories: Iterable[str] | None = None,
        max_memory_mb: int = 512,
        tool_defs: Sequence[MCPToolDefinition] = (),
        tool_handler: ToolHandler | None = None,
    ) -> None:
        self.timeout = timeout
        self.authorized_imports = list(
            self.DEFAULT_IMPORTS if authorized_imports is None else authorized_imports
        )
        self.allowed_directories = list(
            self.DEFAULT_DIRECTORIES
            if allowed_directories is None
            else allowed_directories
        )
        self.max_memory_mb = max_memory_mb
        self.tool_defs = list(tool_defs)
        self.tool_handler = tool_handler
        self._ctx = mp.get_context("spawn")
        self._proc: mp.process.BaseProcess | None = None
        self._conn: ParentConn | None = None
        self.restarts = 0

    @classmethod
    def from_config(
        cls,
        config: SandboxConfig,
        tool_defs: Sequence[MCPToolDefinition] = (),
        tool_handler: ToolHandler | None = None,
    ) -> Sandbox:
        """Build a sandbox from a `sandbox_template.json` already loaded.

        The four limits live in the contract's `SandboxConfig`, so the JSON file
        is the single source of truth; `config.loader.load_sandbox_config` reads
        it. The class defaults below only apply when no config is supplied.
        """
        return cls(
            timeout=float(config.max_execution_time_seconds),
            authorized_imports=config.authorized_imports or None,
            allowed_directories=config.allowed_directories or None,
            max_memory_mb=config.max_memory_mb,
            tool_defs=tool_defs,
            tool_handler=tool_handler,
        )

    def start(self) -> None:
        parent_conn, child_conn = self._ctx.Pipe()
        self._conn = parent_conn
        self._proc = self._ctx.Process(
            target=worker_main,
            args=(
                child_conn,
                self.timeout,
                self.authorized_imports,
                self.allowed_directories,
                self.max_memory_mb,
                self.tool_defs,
            ),
            daemon=True,
        )
        self._proc.start()
        child_conn.close()

    def _kill(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=1)
            if self._proc.is_alive():
                self._proc.kill()
                self._proc.join(timeout=1)
        if self._conn is not None:
            self._conn.close()
        self._proc, self._conn = None, None

    def restart(self) -> None:
        self._kill()
        self.restarts += 1
        self.start()

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(BrokenPipeError, OSError):
                self._conn.send(None)
        self._kill()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def execute(self, code: str) -> ExecResult:
        """Run one code block. Never raises on user-code failure."""
        namespace_reset = self._proc is not None and not self._proc.is_alive()
        if self._proc is None or not self._proc.is_alive():
            self.restart()
        conn = self._conn
        if conn is None:
            raise RuntimeError("sandbox has no live connection to its worker")

        started = time.monotonic()
        try:
            conn.send(ExecRequest(code=code))
        except (BrokenPipeError, OSError):
            self.restart()
            return self._failure_result(
                Outcome.CRASHED,
                "the sandbox worker could not be reached; it was restarted "
                "and all previously defined variables were lost",
                started,
            )

        deadline = self.timeout + HARD_TIMEOUT_MARGIN
        while True:
            if not conn.poll(deadline):
                self.restart()
                return self._failure_result(
                    Outcome.HARD_TIMEOUT,
                    f"code did not return control after {deadline:.0f}s and "
                    "could not be interrupted; the sandbox was restarted and "
                    "all previously defined variables were lost",
                    started,
                )

            try:
                message = conn.recv()
            except (EOFError, OSError):
                self.restart()
                return self._failure_result(
                    Outcome.CRASHED,
                    "the sandbox worker died mid-execution; it was restarted "
                    "and all previously defined variables were lost",
                    started,
                )

            if isinstance(message, ToolCall):
                try:
                    conn.send(ToolReply(result=self._run_tool(message)))
                except (BrokenPipeError, OSError):
                    self.restart()
                    return self._failure_result(
                        Outcome.CRASHED,
                        "the sandbox worker went away while a tool was "
                        "running; it was restarted and all previously "
                        "defined variables were lost",
                        started,
                    )
                continue

            message.namespace_reset = namespace_reset
            return message

    def _run_tool(self, call: ToolCall) -> str:
        """Run one MCP tool here, where the live client and its loop are.

        Never raises: a tool that blows up becomes an observation the model can
        read and react to, exactly like any other tool output.
        """
        if self.tool_handler is None:
            return (
                f"Observation: no MCP server is connected, so the tool "
                f"'{call.name}' is not available."
            )
        try:
            return self.tool_handler(call.name, call.arguments)
        except Exception as exc:  # noqa: BLE001 a broken tool must not end the run
            return f"Observation: tool '{call.name}' failed: {exc}"

    def _failure_result(
        self, outcome: Outcome, message: str, started: float
    ) -> ExecResult:
        """Build a result for a parent-side failure, timing the wait we spent."""
        duration_ms = (time.monotonic() - started) * 1000
        return ExecResult(outcome=outcome, error=message, duration_ms=duration_ms)
