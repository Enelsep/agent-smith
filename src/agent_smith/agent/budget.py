"""Deciding when the next LLM call would blow the task's budget.

Pure functions only: no I/O, nothing here can raise for a data-dependent
reason. That is what keeps this module out of `_Run.execute()`'s
try/except boundary in loop.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_smith.llm import Message

CHARS_PER_TOKEN = 4
"""Divisor behind `estimate_tokens`.

Deliberately the prose ratio, not the denser one code really tokenises at.
A guard wants conservative estimates, so the tempting move is to divide by
3 — but the reservation in `should_force_submission` scales with the
estimate, applying the same bias to the call being authorised and to the
forced call it holds room for, where it largely cancels. Measured against
endpoints billing at 4.0, 3.5 and 3.0 chars per token, dividing by 3
overshoots nothing that dividing by 4 overshoots either, and costs an
iteration in two of the three while leaving up to 45% of the budget
unspent. The extra iteration is worth more than the unused headroom.
"""

DEFAULT_INPUT_MARGIN = 0.15
"""Fraction of the input ceiling held back on top of the reserved call.

The reservation covers the forced request; this covers what scales with
neither — the per-message chat-template overhead the endpoint bills into
`prompt_tokens`, the nudge appended after the estimate was taken, and
variance between one request and the next.
"""

DEFAULT_WALL_CLOCK_MARGIN = 0.15
"""Fraction of the wall-clock ceiling held back, for the same reason:
the forced round trip has to fit inside what is left.
"""

MIN_OUTPUT_RESERVE = 300
"""Floor under `output_reserve`, sized for an MBPP answer: a short
preamble plus a fenced function, 80–250 tokens in practice.
"""

OUTPUT_RESERVE_FRACTION = 0.15
"""Share of the output ceiling held back when that is the larger figure.

What has to fit is one `final_answer(...)`, and how big that is depends on
the benchmark: MBPP submits a function, SWE-bench submits a git patch, and
a non-trivial diff runs 500–2000 tokens. A flat constant sized for MBPP
would guarantee a truncated patch on exactly the branch that exists to
salvage a SWE-bench run.
"""

MIN_VIABLE_OUTPUT_TOKENS = 20
"""Floor below which a forced attempt is not worth a request.

The reserve normally keeps the budget clear of this, but two paths reach
it anyway: a single step draining the budget from above the reserve in
one completion, and a caller configuring a ceiling smaller than the
reserve. A call capped this low cannot carry a `final_answer(...)`, and
at zero most OpenAI-compatible endpoints reject the request outright. See
`can_attempt_submission`.

This is not a smaller spelling of the reserve: the guard trips at
`remaining < output_reserve(...)`, so refusing to call at that same
threshold would mean never making a forced attempt on this branch.
"""


def output_reserve(max_output_tokens: int) -> int:
    """Output budget held back so a forced attempt has room to answer.

    This is the threshold the guard trips on, and it does the real work:
    the budget normally drains a few hundred tokens per step, so stopping
    while this much is left leaves room for a genuine answer. It lands on
    300 for MBPP's 1 500-token ceiling and 1 500 for SWE-bench's 10 000.
    """
    return max(MIN_OUTPUT_RESERVE, int(OUTPUT_RESERVE_FRACTION * max_output_tokens))


FORCED_SUBMISSION_NUDGE = (
    "Your token or time budget for this task is nearly exhausted. Call "
    "final_answer(...) now with your best current solution — this is the "
    "last turn you will get."
)
"""Placeholder wording. CORE-6 owns the real text for both prompts."""


def estimate_tokens(messages: Sequence[Message]) -> int:
    """Approximate the token count of a message list as len(chars) / 4.

    A heuristic, not a tokenizer: no dependency, and no assumption about
    which model's vocabulary applies. It is biased *low* on code — see
    `CHARS_PER_TOKEN` for why that is the right trade here — so it must
    never be read as a conservative upper bound.
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
    reserved_output_tokens: int,
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

    The input test counts the next request *twice* — once for the call
    about to be made, once for the forced submission that would follow
    it. Counting it once bounds `total` from below, not above, and leaves
    the forced call's own prompt entirely outside the ceiling: the run
    would then be stopped by a guard that had already spent the budget it
    was guarding.
    """
    reserved_for_forced_call = estimated_next_input
    if (
        total_input_tokens + estimated_next_input + reserved_for_forced_call
        > max_input_tokens * (1 - input_margin)
    ):
        return "input_tokens"
    if elapsed_seconds > max_wall_clock_seconds * (1 - wall_clock_margin):
        return "wall_clock"
    if remaining_output_tokens < reserved_output_tokens:
        return "output_tokens"
    return None
