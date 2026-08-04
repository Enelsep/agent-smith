# will install security guards here later -- see SBX-3/4/5.

from __future__ import annotations

import contextlib
import io
import signal
import time
import traceback
from typing import TYPE_CHECKING, Any, NoReturn

from .protocol import ExecResult, FinalAnswerSignal, Outcome
from .security import (
    AttributeBlocked,
    ImportBlocked,
    ImportGuard,
    build_sandbox_builtins,
    purge_sys_modules,
    scan_for_escapes,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from multiprocessing.connection import Connection
    from types import FrameType

    from .protocol import ExecRequest

    WorkerConn = Connection[ExecResult, ExecRequest | None]

MAX_OUTPUT_CHARS = 8_000


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    omitted = len(text) - MAX_OUTPUT_CHARS
    clipped = text[:MAX_OUTPUT_CHARS] + f"\n[... truncated,{omitted} chars omitted ...]"
    return clipped, True


def _on_alarm(signum: int, frame: FrameType | None) -> NoReturn:
    """SIGALRM handle: raise inside whatever line is currently running"""
    raise TimeoutError("Execution exceeded the sandbox time limit")


def _build_namespace(guard: ImportGuard) -> dict[str, Any]:
    """Create the persistent globals dict handed to exec()."""

    def final_answer(value: object) -> NoReturn:
        raise FinalAnswerSignal(value)

    return {
        "__name__": "__sandbox__",
        "__builtins__": build_sandbox_builtins(guard),
        "final_answer": final_answer,
    }


def _execute_once(code: str, namespace: dict[str, Any], timeout: float) -> ExecResult:
    """Run one code block in the shared namespace and describe what happened"""
    violation = scan_for_escapes(code)
    if violation is not None:
        return ExecResult(outcome=Outcome.BLOCKED, error=violation)
    out_buf, err_buf = io.StringIO(), io.StringIO()
    started = time.monotonic()

    outcome = Outcome.OK
    error = None
    final_answer = None

    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            exec(code, namespace)  # noqa: S102

    except FinalAnswerSignal as sig:
        outcome = Outcome.FINAL_ANSWER
        final_answer = sig.value

    except TimeoutError as exc:
        outcome = Outcome.SOFT_TIMEOUT
        error = str(exc)

    except (KeyboardInterrupt, SystemExit) as exc:
        outcome = Outcome.SHUTDOWN
        error = f"{type(exc).__name__}: {exc}"

    except (ImportBlocked, AttributeBlocked) as exc:
        outcome = Outcome.BLOCKED
        error = str(exc)

    except Exception:  # noqa: BLE001 : the broad catch is the point
        outcome = Outcome.ERROR
        error = "".join(traceback.format_exc(limit=-8))

    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

    stdout, cut_out = _truncate(out_buf.getvalue())
    stderr, cut_err = _truncate(err_buf.getvalue())

    return ExecResult(
        outcome=outcome,
        stdout=stdout,
        stderr=stderr,
        truncated=cut_out or cut_err,
        error=error,
        final_answer=None if final_answer is None else str(final_answer),
        duration_ms=(time.monotonic() - started) * 1000,
    )


def worker_main(
    conn: WorkerConn, timeout: float, authorized_imports: Iterable[str]
) -> None:
    """entry point for the child process. Loops until the pipe closes."""
    signal.signal(signal.SIGALRM, _on_alarm)
    guard = ImportGuard(authorized_imports)
    purge_sys_modules(guard)
    namespace = _build_namespace(guard)

    while True:
        try:
            request = conn.recv()
        except EOFError:
            break
        if request is None:
            break
        conn.send(_execute_once(request.code, namespace, timeout))

    conn.close()
