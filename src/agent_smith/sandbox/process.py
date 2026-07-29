from __future__ import annotations

import multiprocessing as mp
from typing import TYPE_CHECKING
from typing_extensions import Self

from .protocol import ExecRequest, ExecResult, Outcome
from .worker import worker_main

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from types import TracebackType
    ParentConn = Connection[ExecRequest | None, ExecResult]

HARD_TIMEOUT_MARGIN = 5.0


class Sandbox:
    """A restartable child process holding a persistent execution namespace."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._ctx = mp.get_context("spawn")
        self._proc: mp.process.BaseProcess | None = None
        self._conn: ParentConn | None = None
        self.restarts = 0

    def start(self) -> None:
        parent_conn, child_conn = self._ctx.Pipe()
        self._conn = parent_conn
        self._proc = self._ctx.Process(
            target=worker_main,
            args=(child_conn, self.timeout),
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
        # `start()` always sets it; the check keeps the type narrow below and
        # turns a would-be AttributeError into a clear failure.
        conn = self._conn
        if conn is None:
            raise RuntimeError("sandbox has no live connection to its worker")

        try:
            conn.send(ExecRequest(code=code))
        except (BrokenPipeError, OSError):
            self.restart()
            return self._hard_timeout_result("worker was not reachable")

        deadline = self.timeout + HARD_TIMEOUT_MARGIN
        if not conn.poll(deadline):
            self.restart()
            return self._hard_timeout_result(
                f"code did not return control after {deadline:.0f}s\
                  and could not "
                f"be interrupted; the sandbox was restarted\
                  and all previously "
                f"defined variables were lost"
            )

        try:
            return conn.recv()
        except (EOFError, OSError):
            self.restart()
            return self._hard_timeout_result("worker died mid-execution")

    def _hard_timeout_result(self, message: str) -> ExecResult:
        return ExecResult(outcome=Outcome.HARD_TIMEOUT, error=message)
