"""Every way a step can end, rendered as the text the model reads next."""

from agent_smith.extraction import ExtractionResult
from agent_smith.sandbox import feedback
from agent_smith.sandbox.protocol import ExecResult, Outcome

NO_OUTPUT = (
    "The code ran and printed nothing. A tool returns its result rather than "
    "printing it, so wrap the call in print() to read it."
)

NAMESPACE_LOST = (
    "The sandbox restarted: every variable and function you defined earlier is "
    "gone. Redefine whatever you still need."
)

EMPTY_ANSWER = (
    "You called final_answer() without a value. If you passed get_patch(), the "
    "diff is empty: nothing in the repository has changed yet, so make the edit "
    "first and check that it took. Otherwise call final_answer() again, passing "
    "the answer itself as the argument."
)
"""Said whatever the benchmark. An MBPP run never calls `get_patch`, so the
first sentence is inert there; naming it costs a handful of tokens in an
observation that is already rare, and threading the benchmark down to here
to save them would cost a parameter on every caller."""


def from_extraction(result: ExtractionResult) -> str:
    """The observation for a reply that carried no runnable code.

    CORE-3 writes its failures as messages addressed to the model, so each is
    handed to `feedback.no_code_block` as the reason: it keeps the extractor's
    account of what went wrong and adds the sandbox's own voice and the way
    out.
    """
    # A strategy that matched and then failed means the model framed the block
    # correctly and got the contents wrong; it does not need the format list.
    return feedback.no_code_block(
        result.failure, saw_format=result.strategy is not None
    )


def from_execution(
    executed: ExecResult,
    *,
    namespace_lost: bool = False,
    repair_note: str | None = None,
) -> str:
    """The observation for a step whose code reached the sandbox.

    The repair note leads, because it explains the code the model is about to
    see the result of. The truncation and namespace warnings trail, because
    they apply to what surrounds the result rather than to the result itself.
    """
    parts = [] if repair_note is None else [feedback.repaired_code(repair_note)]
    parts.append(_body(executed))
    if executed.truncated:
        # The worker leaves a marker inline where it cut; this says what the
        # marker means and what to do instead of running the same thing again.
        parts.append(feedback.output_truncated())
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
    if executed.outcome is Outcome.SOFT_TIMEOUT:
        # The worker's own error line says the limit was exceeded. What the
        # model actually needs is that the output above stops mid-run.
        cut = feedback.partial_output(printed=bool(printed))
        return f"{printed}\n\n{cut}" if printed else cut
    detail = executed.error or f"The sandbox reported {executed.outcome.value}."
    return f"{printed}\n\n{detail}" if printed else detail
