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
    from collections.abc import Iterable, Iterator, Sequence
    from multiprocessing.connection import Connection
    from types import FrameType

    from agent_smith.mcp.protocol import MCPToolDefinition

    WorkerConn = Connection[ExecResult | ToolCall, ExecRequest | ToolReply | None]

MAX_OUTPUT_CHARS = 8_000

MEMORY_LIMIT_MESSAGE = (
    "the sandbox exceeded its memory limit and the allocation was refused; "
    "the namespace is intact but the operation did not complete"
)


def _truncate(text: str) -> str:
    """`text`, capped, with a marker saying how much was dropped."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    omitted = len(text) - MAX_OUTPUT_CHARS
    return text[:MAX_OUTPUT_CHARS] + f"\n[... truncated,{omitted} chars omitted ...]"


def _on_alarm(signum: int, frame: FrameType | None) -> NoReturn:
    """SIGALRM handler: raise inside whatever line is currently running."""
    raise TimeoutError("Execution exceeded the sandbox time limit")


@contextlib.contextmanager
def _time_limit(seconds: float) -> Iterator[None]:
    """Interrupt the running line once `seconds` have passed."""
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


@contextlib.contextmanager
def _clock_paused() -> Iterator[None]:
    """Hold the limit while the worker waits on something that is not the code.

    The subject puts MCP server work outside the sandbox timeout, so what was
    left is put back afterwards rather than started again.
    """
    remaining, _ = signal.setitimer(signal.ITIMER_REAL, 0)
    try:
        yield
    finally:
        if remaining > 0:
            signal.setitimer(signal.ITIMER_REAL, remaining)


def _build_namespace(
    policy: SandboxPolicy,
    conn: WorkerConn,
    tool_defs: Sequence[MCPToolDefinition] = (),
) -> dict[str, Any]:
    """Create the persistent globals dict handed to exec().

    `final_answer` is always present; the MCP tool stubs come and go with
    whichever server is connected.
    """

    def final_answer(value: object) -> NoReturn:
        raise FinalAnswerSignal(value)

    def call_tool(name: str, arguments: dict[str, Any]) -> str:
        """What an injected stub calls to reach the parent, and block.

        Both the clock and the policy stand down for the round trip: the wait
        is not the model's code running, and the pipe traffic is the worker's
        own.
        """
        with _clock_paused(), policy.suspended():
            conn.send(ToolCall(name=name, arguments=arguments))
            reply = conn.recv()
        if not isinstance(reply, ToolReply):
            return f"Observation: the sandbox lost the reply for tool '{name}'."
        return reply.result

    namespace: dict[str, Any] = {
        "__name__": "__sandbox__",
        "__builtins__": policy.builtins(),
        "final_answer": final_answer,
    }
    if tool_defs:
        namespace.update(get_sandbox_tool_stubs(list(tool_defs), call_tool))
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
        with (
            _time_limit(timeout),
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
        error = MEMORY_LIMIT_MESSAGE

    except Exception:  # noqa: BLE001 the broad catch is the point: user code must not escape
        outcome = Outcome.ERROR
        error = "".join(traceback.format_exc(limit=-8))

    stdout, stderr = out_buf.getvalue(), err_buf.getvalue()

    return ExecResult(
        outcome=outcome,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        truncated=max(len(stdout), len(stderr)) > MAX_OUTPUT_CHARS,
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
    namespace = _build_namespace(policy, conn, tool_defs)
    policy.install()

    max_bytes = max_memory_mb * 1024 * 1024
    for limit in (resource.RLIMIT_AS, resource.RLIMIT_DATA):
        with contextlib.suppress(ValueError, OSError):
            _, hard = resource.getrlimit(limit)
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
