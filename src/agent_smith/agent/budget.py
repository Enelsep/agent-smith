"""Deciding when the next LLM call would blow the task's budget.

Pure functions only: no I/O, nothing here can raise for a data-dependent
reason. That is what keeps this module out of `_Run.execute()`'s
try/except boundary in loop.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_smith.llm import Message

CHARS_PER_TOKEN = 4


def estimate_tokens(messages: Sequence[Message]) -> int:
    """Approximate the token count of a message list as len(chars) / 4.

    A heuristic, not a tokenizer: no dependency, and no assumption about
    which model's vocabulary applies. The 4-per-token ratio is the
    standard rough estimate for English/code; the guard's margins are
    sized to absorb its error, not to make it exact.
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


MIN_VIABLE_OUTPUT_TOKENS = 20
"""Roughly the shortest a final_answer(...) call can be written in. Below
this, capping max_tokens to the exact remainder no longer buys a real
chance at a completion — treated as its own trigger, on the same
reasoning as running out of input budget or wall clock.
"""

FORCED_SUBMISSION_NUDGE = (
    "Your token or time budget for this task is nearly exhausted. Call "
    "final_answer(...) now with your best current solution — this is the "
    "last turn you will get."
)
"""Placeholder wording. CORE-6 owns the real text for both prompts."""


def should_force_submission(
    *,
    total_input_tokens: int,
    estimated_next_input: int,
    max_input_tokens: int,
    elapsed_seconds: float,
    max_wall_clock_seconds: float,
    remaining_output_tokens: int,
    wall_clock_margin: float = 0.15,
) -> str | None:
    """None if the next call fits comfortably; otherwise a short label
    naming which ceiling triggered the stop, checked in this order:
    "input_tokens", "wall_clock", "output_tokens". The label, not a bare
    bool, is what lets the loop report which budget ran out.
    """
    if total_input_tokens + estimated_next_input > max_input_tokens:
        return "input_tokens"
    if elapsed_seconds > max_wall_clock_seconds * (1 - wall_clock_margin):
        return "wall_clock"
    if remaining_output_tokens < MIN_VIABLE_OUTPUT_TOKENS:
        return "output_tokens"
    return None
