from __future__ import annotations

PREFIX = "[sandbox]"


def notice(happened: str, advice: str) -> str:
    """One notice in the shape all of them share."""
    return f"{PREFIX} {happened.rstrip('. ')}. {advice.rstrip()}"


FORMATS = (
    "a ```python fenced block, an <invoke> block, a <tool_call> block, "
    "or an Action: / Action Input: pair"
)


def no_code_block(reason: str | None = None, *, saw_format: bool = False) -> str:
    """Nothing runnable came back."""
    advice = (
        "Send exactly one well-formed code block and nothing else."
        if saw_format
        else f"Send exactly one code block: {FORMATS}."
    )
    return notice(reason or "No runnable code block was found in your reply", advice)


def only_comments(reason: str | None = None) -> str:
    """The block was well formed and held nothing that runs."""
    return notice(
        reason or "Your code block holds only comments",
        "Comments do not run, so nothing was executed and there is no result "
        "to read. Send the code itself -- the def, the calls, the prints -- "
        "and keep the reasoning short.",
    )


def reply_cut_short() -> str:
    """The reply stopped at the token cap rather than where the model meant."""
    return notice(
        "Your reply was cut off at its token limit, so whatever it ended with "
        "is incomplete",
        "You are billed for every token you write, including the ones spent "
        "before the code. Answer with the code block first and shorter.",
    )


def repaired_code(note: str, code: str | None = None) -> str:
    """A block that did not parse was fixed and run.
    The subject asks for the repair to be explained
    """
    body = notice(
        f"Your code block was malformed, so the sandbox repaired it and ran it "
        f"anyway: {note}",
        "The result below is from the repaired code, not from what you sent. "
        "Send a well-formed block next time.",
    )
    if code is None:
        return body
    return f"{body}\n\nWhat actually ran:\n```python\n{code.strip()}\n```"


def partial_output(*, printed: bool = True) -> str:
    """The clock stopped the code mid-run.

    Whether anything was printed changes the advice completely, so it changes
    the message: partial output can be read and used, no output cannot.
    """
    if printed:
        return notice(
            "Execution hit the time limit and was stopped part-way",
            "The output above is only what ran before it stopped, so treat it "
            "as incomplete. Make the code faster or do less in one turn.",
        )
    return notice(
        "Execution hit the time limit before anything was printed",
        "Print as you go so a slow turn still tells you something, and do less "
        "in one turn.",
    )


def output_truncated(limit_chars: int | None = None) -> str:
    """The output was too large and was cut.

    The model is told to narrow the request rather than to repeat it, because
    repeating produces the same cut and spends another iteration doing it.
    """
    cap = f" of {limit_chars} characters" if limit_chars else ""
    return notice(
        f"The output above was cut at the sandbox size limit{cap}",
        "You are not seeing all of it. Ask for a narrower slice -- a line "
        "range, a filter, or fewer results -- rather than running this again.",
    )


def edit_broke_file(filepath: str, detail: str, *, syntax: bool = True) -> str:
    """The edit was written, and the file no longer holds together.

    The edit is reported as applied because it was: saying only that the file
    is broken would leave the model unsure whether to redo the edit or undo it.
    """
    kind = "a syntax error" if syntax else "a lint violation"
    return notice(
        f"Your edit to '{filepath}' was applied but introduced {kind}: {detail}",
        "Fix it with another edit before running anything that imports it.",
    )
