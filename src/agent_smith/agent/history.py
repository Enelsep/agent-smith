"""Holding the transcript flat, so a long task does not re-send its own past.

The input-token ceiling is cumulative: every iteration re-sends the whole
transcript, so a modest history sent seven times costs far more than the same
history sent once. Older steps are kept for what they contribute causally —
the code that ran and what it printed — and lose the reasoning that led there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_smith.extraction import extract_code

if TYPE_CHECKING:
    from agent_smith.llm import Message

TRUNCATION_MARKER = "[earlier step - code only, prose omitted]"
"""Names what was dropped rather than merely that something was, so a model
reading its own condensed past is not left to account for the gap."""

PREAMBLE_LENGTH = 2
"""The system prompt and the task prompt, which always survive verbatim."""


def compact_history(
    messages: list[Message], *, verbatim_steps: int = 1
) -> list[Message]:
    """Shrink every step older than the verbatim window to its code.

    `messages` arrives as `[system, task, assistant, observation, ...]`. The
    two prompts and the last `verbatim_steps` steps pass through untouched;
    older assistant turns keep `TRUNCATION_MARKER` and whatever code CORE-3
    can still find in them, and their observations are kept whole because they
    are small.

    `verbatim_steps` is a parameter because the benchmarks do not share a
    budget: MBPP allows 6 000 cumulative input tokens, SWE-bench 300 000.

    Never raises, and never mutates its argument. The returned list shares
    message objects with `messages` for the steps it keeps unchanged, so
    treat it as read-only.
    """
    try:
        verbatim_steps = max(0, verbatim_steps)
        return _compact(messages, verbatim_steps=verbatim_steps)
    except Exception:  # noqa: BLE001 - an oversized view beats a lost run
        return messages


def _compact(messages: list[Message], *, verbatim_steps: int) -> list[Message]:
    """The compaction itself, wrapped by the guarantee above."""
    preamble = messages[:PREAMBLE_LENGTH]
    tail = messages[PREAMBLE_LENGTH:]
    steps = [tail[at : at + 2] for at in range(0, len(tail), 2)]

    keep_from = len(steps) - verbatim_steps
    compacted: list[Message] = list(preamble)
    for index, step in enumerate(steps):
        if index >= keep_from:
            compacted.extend(step)
            continue
        reply, *rest = step
        compacted.append({"role": "assistant", "content": _reduced(reply, index)})
        compacted.extend(rest)
    return compacted


def _reduced(reply: Message, index: int) -> str:
    """The marker, plus whatever code the reply still carries."""
    extracted = extract_code(reply["content"], step=index + 1)
    if extracted.code is None:
        return TRUNCATION_MARKER
    return f"{TRUNCATION_MARKER}\n{extracted.code}"
