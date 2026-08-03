"""Deciding when the next LLM call would blow the task's budget.

Pure functions only: no I/O, nothing here can raise for a data-dependent
reason. That is what keeps this module out of `_Run.execute()`'s
try/except boundary in loop.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_smith.llm import Message

CHARS_PER_TOKEN = 4

DEFAULT_INPUT_MARGIN = 0.15
"""Fraction of the input ceiling held back for the forced round trip.

An over-budget run is scored as a failure whatever it answered, so the
guard trips while one more request still fits: the submission it forces
is itself a request, and it has to land inside the ceiling.

The margin also covers `estimate_tokens` being optimistic rather than
slack: chars/4 is the ratio for prose, code tokenises nearer 3 chars per
token, and the estimate ignores the per-message chat-template overhead
the endpoint bills into `prompt_tokens`.
"""

DEFAULT_WALL_CLOCK_MARGIN = 0.15
"""Fraction of the wall-clock ceiling held back, for the same reason:
the forced round trip has to fit inside what is left.
"""

RESERVED_OUTPUT_TOKENS = 300
"""Output budget held back so a forced attempt has room to answer.

This is the threshold the guard *trips* on, and it does the real work:
the budget normally drains a few hundred tokens per step, so stopping
while this much is left leaves the forced attempt room for a genuine
fenced code block.
"""

MIN_VIABLE_OUTPUT_TOKENS = 20
"""Floor below which a forced attempt is not worth a request.

`RESERVED_OUTPUT_TOKENS` normally keeps the budget clear of this, but two
paths reach it anyway: a single step draining the budget from above the
reserve in one completion, and a caller configuring a ceiling smaller
than the reserve. A call capped this low cannot carry a
`final_answer(...)`, and at zero most OpenAI-compatible endpoints reject
the request outright. See `can_attempt_submission`.

The two constants are not interchangeable: the guard trips at
`remaining < RESERVED_OUTPUT_TOKENS`, so refusing to call at that same
threshold would mean never making a forced attempt on this branch.
"""

FORCED_SUBMISSION_NUDGE = (
    "Your token or time budget for this task is nearly exhausted. Call "
    "final_answer(...) now with your best current solution — this is the "
    "last turn you will get."
)
"""Placeholder wording. CORE-6 owns the real text for both prompts."""


def estimate_tokens(messages: Sequence[Message]) -> int:
    """Approximate the token count of a message list as len(chars) / 4.

    A heuristic, not a tokenizer: no dependency, and no assumption about
    which model's vocabulary applies. It is biased *low* — see
    `DEFAULT_INPUT_MARGIN`, which is sized to cover that bias — so it
    must never be read as a conservative upper bound.
    """
    total_chars = sum(len(message["content"]) for message in messages)
    return total_chars // CHARS_PER_TOKEN


def remaining_output_tokens(spent: int, limit: int) -> int:
    """How much of the output-token ceiling is left, never negative."""
    return max(0, limit - spent)


def capped_max_tokens(default: int | None, remaining: int) -> int:
    """The smaller of the caller's own max_tokens and what the output
    budget has left, so a single verbose completion cannot overshoot the
    ceiling that every prior step has been building toward.
    """
    if default is None:
        return remaining
    return min(default, remaining)


def can_attempt_submission(remaining_output_tokens: int) -> bool:
    """Whether a forced attempt has the output budget to be worth making.

    A call capped below `MIN_VIABLE_OUTPUT_TOKENS` cannot return a
    `final_answer(...)`, and one capped at zero is rejected outright by
    most OpenAI-compatible endpoints. Either way it can only spend input
    tokens and wall clock the run no longer has, so the caller ends the
    run instead of making it.
    """
    return remaining_output_tokens >= MIN_VIABLE_OUTPUT_TOKENS


def should_force_submission(
    *,
    total_input_tokens: int,
    estimated_next_input: int,
    max_input_tokens: int,
    elapsed_seconds: float,
    max_wall_clock_seconds: float,
    remaining_output_tokens: int,
    input_margin: float = DEFAULT_INPUT_MARGIN,
    wall_clock_margin: float = DEFAULT_WALL_CLOCK_MARGIN,
) -> str | None:
    """None if the next call fits comfortably; otherwise a short label
    naming which ceiling triggered the stop, checked in this order:
    "input_tokens", "wall_clock", "output_tokens". The label, not a bare
    bool, is what lets the loop report which budget ran out.

    Every threshold trips while a forced round trip still fits, never at
    the ceiling itself: the point is to submit *under* budget, and a run
    that crosses any of the three is scored as a failure regardless of
    what it answered.
    """
    if total_input_tokens + estimated_next_input > max_input_tokens * (
        1 - input_margin
    ):
        return "input_tokens"
    if elapsed_seconds > max_wall_clock_seconds * (1 - wall_clock_margin):
        return "wall_clock"
    if remaining_output_tokens < RESERVED_OUTPUT_TOKENS:
        return "output_tokens"
    return None
