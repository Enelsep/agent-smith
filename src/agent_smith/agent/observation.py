"""Every way a step can end, rendered as the text the model reads next."""

from agent_smith.extraction import ExtractionResult
from agent_smith.sandbox.protocol import ExecResult, Outcome

NO_OUTPUT = "The code ran and printed nothing."

NAMESPACE_LOST = (
    "The sandbox restarted: every variable and function you defined earlier is "
    "gone. Redefine whatever you still need."
)

EMPTY_ANSWER = (
    "You called final_answer() without a value. Call it again, passing the "
    "answer itself as the argument."
)


def from_extraction(result: ExtractionResult) -> str:
    """The observation for a reply that carried no runnable code.

    CORE-3 writes its failures as messages addressed to the model, so they
    pass through unchanged.
    """
    return result.failure or "I could not find any code to run in your reply."


def from_execution(
    executed: ExecResult,
    *,
    namespace_lost: bool = False,
    repair_note: str | None = None,
) -> str:
    """The observation for a step whose code reached the sandbox.

    The repair note leads, because it explains the code the model is about to
    see the result of. The namespace warning trails, because it applies to
    everything that follows rather than to this result.
    """
    parts = [] if repair_note is None else [repair_note]
    parts.append(_body(executed))
    if namespace_lost:
        parts.append(NAMESPACE_LOST)
    return "\n\n".join(parts)


def combined_output(executed: ExecResult) -> str:
    """`stdout` and `stderr`, stripped and joined the way the model reads them.

    The worker captures the two streams separately; joining them here is what
    lets a step that only wrote to stderr still show up as output.
    """
    return "\n".join(
        stream.strip()
        for stream in (executed.stdout, executed.stderr)
        if stream.strip()
    )


def _body(executed: ExecResult) -> str:
    """What the sandbox itself has to say about this execution.

    Output is not truncated here: the worker already caps each stream at
    `MAX_OUTPUT_CHARS` with an explicit marker.
    """
    if executed.outcome is Outcome.FINAL_ANSWER:
        if executed.final_answer:
            # The loop consumes an answer that carries a value and never asks
            # for an observation, so this renders only for a caller that does.
            return executed.final_answer
        return EMPTY_ANSWER
    printed = combined_output(executed)
    if executed.outcome is Outcome.OK:
        return printed or NO_OUTPUT
    detail = executed.error or f"The sandbox reported {executed.outcome.value}."
    return f"{printed}\n\n{detail}" if printed else detail
