from __future__ import annotations

import contextlib
import io
import resource
import signal
import time
import traceback
from typing import TYPE_CHECKING, Any, NoReturn

from agent_smith.mcp.sandbox_integration import get_sandbox_tool_stubs

from .protocol import (
    ExecRequest,
    ExecResult,
    FinalAnswerSignal,
    Outcome,
    ToolCall,
    ToolReply,
)
from .security import SandboxBlocked, SandboxPolicy

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from multiprocessing.connection import Connection
    from types import FrameType

    from agent_smith.mcp.protocol import MCPToolDefinition

    WorkerConn = Connection[ExecResult | ToolCall, ExecRequest | ToolReply | None]

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


def _make_tool_bridge(
    conn: WorkerConn, policy: SandboxPolicy
) -> Callable[[str, dict[str, Any]], str]:
    """Build the function the injected tool stubs call to reach the parent.

    The timer and the policy both pause for the round trip: the subject says
    MCP server work is not subject to the sandbox timeout, and the pipe
    traffic here is the worker's own, not the model's.
    """

    def request(name: str, arguments: dict[str, Any]) -> str:
        remaining, _ = signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            with policy.suspended():
                conn.send(ToolCall(name=name, arguments=arguments))
                reply = conn.recv()
        finally:
            if remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining)

        if not isinstance(reply, ToolReply):
            return f"Observation: the sandbox lost the reply for tool '{name}'."
        return reply.result

    return request


def _build_namespace(
    policy: SandboxPolicy,
    tool_defs: Sequence[MCPToolDefinition] = (),
    conn: WorkerConn | None = None,
) -> dict[str, Any]:
    """Create the persistent globals dict handed to exec().

    `final_answer` is always present; the MCP tool stubs come and go with
    whichever server is connected.
    """

    def final_answer(value: object) -> NoReturn:
        raise FinalAnswerSignal(value)

    namespace: dict[str, Any] = {
        "__name__": "__sandbox__",
        "__builtins__": policy.builtins(),
        "final_answer": final_answer,
    }
    if tool_defs and conn is not None:
        namespace.update(
            get_sandbox_tool_stubs(list(tool_defs), _make_tool_bridge(conn, policy))
        )
    return namespace


def _execute_once(
    code: str, namespace: dict[str, Any], timeout: float, policy: SandboxPolicy
) -> ExecResult:
    """Run one code block in the shared namespace and describe what happened"""
    violation = policy.check_code(code)
    if violation is not None:
        return ExecResult(outcome=Outcome.BLOCKED, error=violation)
    out_buf, err_buf = io.StringIO(), io.StringIO()
    started = time.monotonic()

    outcome = Outcome.OK
    error = None
    final_answer = None

    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        with (
            contextlib.redirect_stdout(out_buf),
            contextlib.redirect_stderr(err_buf),
            policy.enforcing(),
        ):
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

    except SandboxBlocked as exc:
        outcome = Outcome.BLOCKED
        error = str(exc)

    except MemoryError:
        outcome = Outcome.MEMORY_LIMIT
        error = (
            "the sandbox exceeded its memory limit and the allocation was "
            "refused; the namespace is intact but the operation did not complete"
        )

    except Exception:  # noqa: BLE001 the broad catch is the point: user code must not escape
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
    conn: WorkerConn,
    timeout: float,
    authorized_imports: Iterable[str],
    allowed_directories: Iterable[str] = (),
    max_memory_mb: int = 512,
    tool_defs: Sequence[MCPToolDefinition] = (),
) -> None:
    """Entry point for the child process. Loops until the pipe closes.

    Guard installation order matters: the namespace must exist before any
    sandboxed code runs, and the memory cap goes on last so the worker's own
    startup allocations are not counted against the sandbox's budget.
    """
    signal.signal(signal.SIGALRM, _on_alarm)

    policy = SandboxPolicy(authorized_imports, allowed_directories)
    namespace = _build_namespace(policy, tool_defs, conn)
    policy.install()
    max_bytes = max_memory_mb * 1024 * 1024
    for limit in (resource.RLIMIT_AS, resource.RLIMIT_DATA):
        with contextlib.suppress(ValueError, OSError):
            _soft, hard = resource.getrlimit(limit)
            ceiling = (
                max_bytes if hard == resource.RLIM_INFINITY else min(max_bytes, hard)
            )
            resource.setrlimit(limit, (ceiling, ceiling))

    while True:
        try:
            request = conn.recv()
        except EOFError:
            break
        if request is None:
            break
        if not isinstance(request, ExecRequest):
            continue
        conn.send(_execute_once(request.code, namespace, timeout, policy))

    conn.close()
