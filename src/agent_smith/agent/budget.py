from __future__ import annotations

import math
from collections.abc import Sequence

from agent_smith.llm import Message

CHARS_PER_TOKEN = 4
"""Divisor behind `estimate_tokens`."""

UNCALIBRATED_BILLING_RATIO = 1.6
"""Assumed billed-per-estimated ratio before any call has been billed.
Deliberately pessimistic
"""

DEFAULT_INPUT_MARGIN = 0.15
"""Fraction of the input ceiling held back after everything measurable.
"""

DEFAULT_WALL_CLOCK_MARGIN = 0.15
"""Fraction of the wall-clock ceiling held back for the forced round trip.
"""

MIN_OUTPUT_RESERVE = 300
"""Floor under `output_reserve`, sized for an MBPP answer: a short
preamble plus a fenced function, 80–250 tokens in practice.
"""

OUTPUT_RESERVE_FRACTION = 0.15
"""Share of the output ceiling held back when that is the larger figure.
"""

MIN_VIABLE_OUTPUT_TOKENS = 20
"""Floor below which a forced attempt is not worth a request.
"""


def output_reserve(max_output_tokens: int) -> int:
    """Output budget held back so a forced attempt has room to answer."""
    return max(MIN_OUTPUT_RESERVE, int(OUTPUT_RESERVE_FRACTION * max_output_tokens))


FORCED_SUBMISSION_NUDGE = (
    "Your budget for this task is nearly exhausted. This is your last turn: "
    "whatever you send now is the answer, and there will be no observation "
    "after it. Submit the best solution you already have, even if it is "
    "unfinished. Answer with one block of exactly this shape, your own "
    "function in place of add:\n"
    "\n"
    "```python\n"
    "final_answer('''def add(a, b):\n"
    "    return a + b\n"
    "''')\n"
    "```<end_code>"
)
"""What the model is told on the turn the budget guard forces.
"""

NUDGE_TOKENS = len(FORCED_SUBMISSION_NUDGE) // CHARS_PER_TOKEN
"""What the nudge adds to the forced request, in the estimator's own unit.

Derived rather than written down, so CORE-6 rewording the nudge cannot
silently change what the guard reserves for it.
"""


def estimate_tokens(messages: Sequence[Message]) -> int:
    """Approximate the token count of a message list as len(chars) / 4."""
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


def billing_ratio(billed: int, estimated: int) -> float:
    """What one call was billed, per token `estimate_tokens` predicted."""
    if estimated <= 0:
        return UNCALIBRATED_BILLING_RATIO
    return max(1.0, billed / estimated)


def can_afford_forced_call(
    *,
    total_input_tokens: int,
    estimated_next_input: int,
    ratio: float,
    max_input_tokens: int,
) -> bool:
    """Whether the forced submission fits under the input ceiling."""
    forced_call = math.ceil(ratio * (estimated_next_input + NUDGE_TOKENS + 1))
    return total_input_tokens + forced_call <= max_input_tokens


def can_attempt_submission(remaining_output_tokens: int) -> bool:
    """Whether a forced attempt has the output budget to be worth making."""
    return remaining_output_tokens >= MIN_VIABLE_OUTPUT_TOKENS


def should_force_submission(
    *,
    total_input_tokens: int,
    estimated_next_input: int,
    max_input_tokens: int,
    elapsed_seconds: float,
    max_wall_clock_seconds: float,
    estimated_growth: int,
    ratio: float,
    remaining_output_tokens: int,
    reserved_output_tokens: int,
    input_margin: float = DEFAULT_INPUT_MARGIN,
    wall_clock_margin: float = DEFAULT_WALL_CLOCK_MARGIN,
) -> str | None:
    """None if the next call fits comfortably; otherwise a short label
    naming which ceiling triggered the stop, checked in this order:
    "input_tokens", "wall_clock", "output_tokens". The label, not a bare
    bool, is what lets the loop report which budget ran out.

    The input test asks one question: *if I authorise this call, can I
    still afford the forced submission that would follow it?*
    """
    authorised_call = math.ceil(ratio * estimated_next_input)
    forced_call = math.ceil(
        ratio * (estimated_next_input + estimated_growth + NUDGE_TOKENS + 1)
    )
    if total_input_tokens + authorised_call + forced_call > max_input_tokens * (
        1 - input_margin
    ):
        return "input_tokens"
    if elapsed_seconds > max_wall_clock_seconds * (1 - wall_clock_margin):
        return "wall_clock"
    if remaining_output_tokens < reserved_output_tokens:
        return "output_tokens"
    return None
