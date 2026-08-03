from __future__ import annotations

import multiprocessing as mp
import time
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import Self

from .protocol import ExecRequest, ExecResult, Outcome
from .worker import worker_main

if TYPE_CHECKING:
    from collections.abc import Iterable
    from multiprocessing.connection import Connection
    from types import TracebackType

    ParentConn = Connection[ExecRequest | None, ExecResult]

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

    def __init__(
        self,
        timeout: float = 30.0,
        authorized_imports: Iterable[str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.authorized_imports = list(
            self.DEFAULT_IMPORTS if authorized_imports is None else authorized_imports
        )
        self._ctx = mp.get_context("spawn")
        self._proc: mp.process.BaseProcess | None = None
        self._conn: ParentConn | None = None
        self.restarts = 0

    def start(self) -> None:
        parent_conn, child_conn = self._ctx.Pipe()
        self._conn = parent_conn
        self._proc = self._ctx.Process(
            target=worker_main,
            args=(child_conn, self.timeout, self.authorized_imports),
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
            try:
                self._conn.send(None)
            except (BrokenPipeError, OSError):
                pass
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
        if not conn.poll(deadline):
            self.restart()
            return self._failure_result(
                Outcome.HARD_TIMEOUT,
                f"code did not return control after {deadline:.0f}s and could "
                "not be interrupted; the sandbox was restarted and all "
                "previously defined variables were lost",
                started,
            )

        try:
            return conn.recv()
        except (EOFError, OSError):
            self.restart()
            return self._failure_result(
                Outcome.CRASHED,
                "the sandbox worker died mid-execution; it was restarted and "
                "all previously defined variables were lost",
                started,
            )

    def _failure_result(
        self, outcome: Outcome, message: str, started: float
    ) -> ExecResult:
        """Build a result for a parent-side failure, timing the wait we spent."""
        duration_ms = (time.monotonic() - started) * 1000
        return ExecResult(outcome=outcome, error=message, duration_ms=duration_ms)
