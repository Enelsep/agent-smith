from __future__ import annotations

from agent_smith.sandbox.protocol import ExecResult, Outcome

MARKER = "sandbox:"

TRUNCATED = f"{MARKER} the output above was truncated."

NAMESPACE_RESET = (
    f"{MARKER} the worker restarted, so every variable and function defined "
    f"earlier is gone."
)

EMPTY_ANSWER = f"{MARKER} final_answer() was called without a value."


def render(result: ExecResult) -> str:
    """Everything this execution has to say, or `""` when it has nothing.

    A statement that prints nothing and raises nothing renders empty, the way
    an assignment at any Python prompt does.
    """
    parts = [part for part in (streams(result), _detail(result)) if part]
    if result.truncated:
        parts.append(TRUNCATED)
    if result.namespace_reset:
        parts.append(NAMESPACE_RESET)
    return "\n".join(parts)


def streams(result: ExecResult) -> str:
    """What the code itself wrote, both streams, in the order they are read.

    The worker captures stdout and stderr separately; joining them here is what
    lets an entry that only wrote to stderr still show up.
    """
    return "\n".join(
        stream.rstrip("\n")
        for stream in (result.stdout, result.stderr)
        if stream.strip()
    )


def _detail(result: ExecResult) -> str:
    """What the sandbox has to say about how the entry ended."""
    if result.outcome is Outcome.OK:
        return ""

    if result.outcome is Outcome.FINAL_ANSWER:
        if result.final_answer is None:
            return EMPTY_ANSWER
        return f"{MARKER} final answer:\n{result.final_answer}"

    if result.outcome is Outcome.ERROR:
        return (result.error or "the code raised, with no detail").rstrip("\n")
    detail = result.error or "no further detail"
    return f"{MARKER} {result.outcome.value}: {detail}"
