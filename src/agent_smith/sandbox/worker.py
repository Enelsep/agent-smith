# will install security guards here later -- see SBX-3/4/5.

from __future__ import annotations

import builtins
import contextlib
import io
import signal
import time
import traceback
from typing import Dict, List

from .protocol import ExecResult, FinalAnswerSignal, Outcome

MAX_OUTPUT_CHARS = 8_000


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    omitted = len(text) - MAX_OUTPUT_CHARS
    return text[:MAX_OUTPUT_CHARS] + "\n[... truncated,"\
                                     f"{omitted} chars omitted ...]"


def _on_alarm(signum, frame):
    """SIGALRM handle: raise inside whatever line is currently running"""
    raise TimeoutError("Execution exceeded the sandbox time limit")


def _build_namespace(final_answer_box: list) -> Dict:
    """Create the persistent globals dict handed to exec()."""

    def final_answer(value):
        final_answer_box.append(value)
        raise FinalAnswerSignal(value)

    return {
        "__name__": "__sandbox__",
        # will swapp this for a filtered dict later.
        "__builtins__": builtins.__dict__,
        "final_answer": final_answer,
    }


def _execute_once(code: str, namespace: dict,
                  timeout: float, box: list) -> ExecResult:
    """Run one code block in the shared namespace and describe what happened"""
    out_buf, err_buf = io.StringIO(), io.StringIO()
    started = time.monotonic()

    outcome = Outcome.OK
    error = None
    final_answer = None

    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        with contextlib.redirect_stdout(out_buf), \
             contextlib.redirect_stderr(err_buf):
            exec(code, namespace)

    except FinalAnswerSignal:
        outcome = Outcome.FINAL_ANSWER
        final_answer = box[-1] if box else None

    except TimeoutError as exc:
        outcome = Outcome.SOFT_TIMEOUT
        error = str(exc)

    except (KeyboardInterrupt, SystemExit) as exc:
        outcome = Outcome.SHUTDOWN
        error = f"{type(exc).__name__}: {exc}"

    except Exception:
        outcome = Outcome.ERROR
        error = "".join(traceback.format_exc(limit=8))

    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

    return ExecResult(
        outcome=outcome,
        stdout=_truncate(out_buf.getvalue()),
        stderr=_truncate(err_buf.getvalue()),
        error=error,
        final_answer=None if final_answer is None else str(final_answer),
        duration_ms=(time.monotonic() - started) * 1000,
    )


def worker_main(conn, timeout: float) -> None:
    """entry point for the child process. Loops until the pipe closes."""
    signal.signal(signal.SIGALRM, _on_alarm)
    box: List = []
    namespace = _build_namespace(box)

    while True:
        try:
            request = conn.recv()
        except EOFError:
            break
        if request is None:
            break

        box.clear()
        result = _execute_once(request.code, namespace, timeout, box)
        conn.send(result)

    conn.close()
