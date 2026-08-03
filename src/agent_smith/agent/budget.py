"""Deciding when the next LLM call would blow the task's budget.

Pure functions only: no I/O, nothing here can raise for a data-dependent
reason. That is what keeps this module out of `_Run.execute()`'s
try/except boundary in loop.py.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from agent_smith.llm import Message

CHARS_PER_TOKEN = 4
"""Divisor behind `estimate_tokens`.

Its exact value matters little, because `should_force_submission` works in
*billed* tokens and converts estimates with a ratio measured against what
the endpoint actually charged — see `billing_ratio`. A wrong divisor is
absorbed by that measurement after the first call; what it must not do is
be mistaken for a billed figure.
"""

UNCALIBRATED_BILLING_RATIO = 1.6
"""Assumed billed-per-estimated ratio before any call has been billed.

Deliberately pessimistic: it stands for an endpoint tokenising at 2.5
chars per token, denser than the 3.0–3.5 real code reaches. It applies
only to the first iteration, where the cumulative total is near zero and
an over-estimate cannot cost anything.
"""

DEFAULT_INPUT_MARGIN = 0.15
"""Fraction of the input ceiling held back after everything measurable.

`should_force_submission` accounts for the two quantities that used to be
left to this margin — the billing ratio and the transcript's growth
between the authorised call and the forced one. What is left for the
margin is what neither measures: the per-message chat-template overhead
folded into `prompt_tokens`, and variance between one request and the next.
"""

DEFAULT_WALL_CLOCK_MARGIN = 0.15
"""Fraction of the wall-clock ceiling held back for the forced round trip.

Unlike the input budget there is nothing to measure here: how long a call
will take is not knowable before making it, so the whole reservation is
this margin.
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

NUDGE_TOKENS = len(FORCED_SUBMISSION_NUDGE) // CHARS_PER_TOKEN
"""What the nudge adds to the forced request, in the estimator's own unit.

Derived rather than written down, so CORE-6 rewording the nudge cannot
silently change what the guard reserves for it.
"""


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


def billing_ratio(billed: int, estimated: int) -> float:
    """What one call was billed, per token `estimate_tokens` predicted.

    The ceiling is denominated in what the endpoint charges, while the
    guard can only measure what it is about to send, and the two differ by
    the model's tokenisation, the chat template, and whatever else the
    endpoint folds into `prompt_tokens`. Both numbers are known after every
    call, so the conversion is measured rather than assumed.

    Per call, not cumulative: the caller keeps the largest value seen. A
    running average of the totals would lag a ratio that rises during the
    run — longer prompts tokenising denser than short ones, say — and lag
    in this direction means under-reserving.

    Floored at 1.0: an endpoint billing *less* than predicted is a windfall
    to bank, not a licence to send more.
    """
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
    """Whether the forced submission fits under the input ceiling.

    The input counterpart of `can_attempt_submission`, and pointless for
    the same reason when it fails: a run that crosses the ceiling is
    scored a failure whatever it answered, so a request that cannot fit
    cannot help. Reserving ahead keeps this rare — it is reached when the
    endpoint bills far above what the first, uncalibrated call assumed —
    but the guarantee should not rest on the estimate being close.

    Measured against the ceiling itself, not the margin: this is the last
    call, so there is nothing further to hold room for.
    """
    forced_call = math.ceil(ratio * (estimated_next_input + NUDGE_TOKENS + 1))
    return total_input_tokens + forced_call <= max_input_tokens


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

    Every threshold trips while a forced round trip still fits, never at
    the ceiling itself: the point is to submit *under* budget, and a run
    that crosses any of the three is scored as a failure regardless of
    what it answered.

    The input test asks one question: *if I authorise this call, can I
    still afford the forced submission that would follow it?* Both costs
    are converted to billed tokens with `ratio`, because that is the unit
    `total_input_tokens` and the ceiling are denominated in — comparing a
    raw estimate against a billed ceiling silently under-reserves by
    however much the endpoint charges above chars/4.

    The forced call is not this call: by the time it is made the model's
    reply and the resulting observation have joined the transcript, so it
    is reserved at `estimated_next_input + estimated_growth`, plus the
    nudge that will be appended to it.
    """
    # Rounded up, so a cost is never carried as less than a whole token.
    # Not for float precision — the error there is ~1e-12 against a margin
    # of hundreds — but because `estimate_tokens` floors a summed character
    # count, so the view and the nudge estimated apart can come to one token
    # less than the request they are actually sent as.
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
