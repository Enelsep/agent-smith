"""Pure functions deciding when the next LLM call would blow the task budget."""

import ast

from agent_smith.agent.budget import (
    FORCED_SUBMISSION_NUDGE,
    MIN_OUTPUT_RESERVE,
    MIN_VIABLE_OUTPUT_TOKENS,
    UNCALIBRATED_BILLING_RATIO,
    billing_ratio,
    can_afford_forced_call,
    can_attempt_submission,
    capped_max_tokens,
    estimate_tokens,
    output_reserve,
    remaining_output_tokens,
    should_force_submission,
)
from agent_smith.llm import Message


def test_estimate_tokens_uses_a_four_chars_per_token_ratio() -> None:
    messages: list[Message] = [{"role": "user", "content": "a" * 40}]
    assert estimate_tokens(messages) == 10


def test_estimate_tokens_sums_every_message_in_the_list() -> None:
    messages: list[Message] = [
        {"role": "system", "content": "a" * 20},
        {"role": "user", "content": "a" * 20},
    ]
    assert estimate_tokens(messages) == 10


def test_remaining_output_tokens_floors_at_zero() -> None:
    assert remaining_output_tokens(spent=1500, limit=1500) == 0
    assert remaining_output_tokens(spent=2000, limit=1500) == 0


def test_remaining_output_tokens_is_the_difference_when_positive() -> None:
    assert remaining_output_tokens(spent=400, limit=1500) == 1100


def test_capped_max_tokens_returns_remaining_when_default_is_none() -> None:
    assert capped_max_tokens(default=None, remaining=300) == 300


def test_capped_max_tokens_returns_the_smaller_of_the_two() -> None:
    assert capped_max_tokens(default=500, remaining=300) == 300
    assert capped_max_tokens(default=200, remaining=300) == 200


def test_a_submission_is_worth_attempting_from_the_viable_floor_up() -> None:
    assert can_attempt_submission(MIN_VIABLE_OUTPUT_TOKENS) is True
    assert can_attempt_submission(MIN_VIABLE_OUTPUT_TOKENS - 1) is False


def test_the_billing_ratio_is_measured_from_what_was_charged() -> None:
    assert billing_ratio(billed=1600, estimated=1000) == 1.6


def test_a_generous_endpoint_does_not_licence_sending_more() -> None:
    # Billing under the estimate is a windfall to bank, not headroom to
    # spend: the next endpoint in the rotation may not be so generous.
    assert billing_ratio(billed=500, estimated=1000) == 1.0


def test_the_billing_ratio_is_pessimistic_before_anything_is_measured() -> None:
    assert billing_ratio(billed=0, estimated=0) == UNCALIBRATED_BILLING_RATIO


def test_a_forced_call_that_would_cross_the_ceiling_is_not_worth_making() -> None:
    # Reached when the endpoint bills far above what the first,
    # uncalibrated call assumed. The run is lost either way, but a request
    # that carries the total past the ceiling cannot be scored a pass, so
    # it cannot help.
    assert (
        can_afford_forced_call(
            total_input_tokens=5000,
            estimated_next_input=400,
            ratio=1.0,
            max_input_tokens=6000,
        )
        is True
    )
    assert (
        can_afford_forced_call(
            total_input_tokens=5000,
            estimated_next_input=400,
            ratio=2.5,
            max_input_tokens=6000,
        )
        is False
    )


def test_an_exhausted_output_budget_is_never_worth_a_request() -> None:
    # The case that would otherwise send max_tokens=0, which most
    # OpenAI-compatible endpoints reject outright.
    assert can_attempt_submission(0) is False


def test_the_output_reserve_is_a_floor_on_a_small_ceiling() -> None:
    # MBPP: 15% of 1500 is 225, under the floor sized for a fenced function.
    assert output_reserve(1500) == MIN_OUTPUT_RESERVE


def test_the_output_reserve_scales_with_a_large_ceiling() -> None:
    # SWE-bench submits a git patch, not a function. A flat 300 would
    # guarantee a truncated diff on the branch meant to salvage the run.
    assert output_reserve(10000) == 1500


def force_reason(
    *,
    total_input_tokens: int = 100,
    estimated_next_input: int = 100,
    estimated_growth: int = 100,
    ratio: float = 1.0,
    max_input_tokens: int = 6000,
    elapsed_seconds: float = 10.0,
    max_wall_clock_seconds: float = 120.0,
    remaining_output_tokens: int = 1000,
    reserved_output_tokens: int = MIN_OUTPUT_RESERVE,
) -> str | None:
    """`should_force_submission` with every ceiling healthy by default, so
    each test names only the one it breaches."""
    return should_force_submission(
        total_input_tokens=total_input_tokens,
        estimated_next_input=estimated_next_input,
        estimated_growth=estimated_growth,
        ratio=ratio,
        max_input_tokens=max_input_tokens,
        elapsed_seconds=elapsed_seconds,
        max_wall_clock_seconds=max_wall_clock_seconds,
        remaining_output_tokens=remaining_output_tokens,
        reserved_output_tokens=reserved_output_tokens,
    )


def test_nothing_is_forced_while_every_ceiling_is_far_off() -> None:
    assert force_reason() is None


def test_the_input_guard_reserves_the_forced_call_it_will_ask_for() -> None:
    # 4500 + 250 is under the 15% margin of 5100, so charging only for the
    # call in front would let this iteration through — and the forced call
    # that follows would then be spent outside any budget the guard ever
    # checked. Reserving it is what trips this.
    assert 4500 + 250 < 5100
    assert force_reason(total_input_tokens=4500, estimated_next_input=250) == (
        "input_tokens"
    )


def test_the_input_guard_leaves_room_for_the_round_trip_it_reserved() -> None:
    assert force_reason(total_input_tokens=4000, estimated_next_input=250) is None


def test_the_input_guard_converts_estimates_into_billed_tokens() -> None:
    # The ceiling counts what the endpoint charges. The same transcript
    # that fits when billing matches the estimate does not fit when the
    # endpoint bills 60% above it, and the guard has to see that.
    assert (
        force_reason(total_input_tokens=3400, estimated_next_input=500, ratio=1.0)
        is None
    )
    assert (
        force_reason(total_input_tokens=3400, estimated_next_input=500, ratio=1.6)
        == "input_tokens"
    )


def test_the_input_guard_reserves_the_growth_before_the_forced_call() -> None:
    # The forced call is not this view: by the time it is made, a reply and
    # an observation have joined the transcript.
    assert (
        force_reason(
            total_input_tokens=4200, estimated_next_input=300, estimated_growth=0
        )
        is None
    )
    assert (
        force_reason(
            total_input_tokens=4200, estimated_next_input=300, estimated_growth=600
        )
        == "input_tokens"
    )


def test_the_wall_clock_guard_trips_past_its_margin() -> None:
    assert force_reason(elapsed_seconds=110.0) == "wall_clock"


def test_the_wall_clock_guard_allows_exactly_its_margin() -> None:
    # 0.85 * 120 is exactly 102, and the comparison is strict.
    assert force_reason(elapsed_seconds=102.0) is None


def test_the_output_guard_trips_below_the_reserve() -> None:
    assert force_reason(remaining_output_tokens=MIN_OUTPUT_RESERVE - 1) == (
        "output_tokens"
    )


def test_the_output_guard_allows_exactly_the_reserve() -> None:
    assert force_reason(remaining_output_tokens=MIN_OUTPUT_RESERVE) is None


def test_the_output_guard_honours_the_reserve_it_is_given() -> None:
    # SWE-bench passes a larger reserve; the same remainder trips there
    # and not on MBPP.
    assert force_reason(remaining_output_tokens=800, reserved_output_tokens=1500) == (
        "output_tokens"
    )
    assert force_reason(remaining_output_tokens=800, reserved_output_tokens=300) is None


def test_input_tokens_are_reported_ahead_of_wall_clock() -> None:
    # Both would trip on their own; input_tokens is checked first.
    assert (
        force_reason(
            total_input_tokens=4700,
            estimated_next_input=250,
            elapsed_seconds=110.0,
        )
        == "input_tokens"
    )


def test_wall_clock_is_reported_ahead_of_output_tokens() -> None:
    assert (
        force_reason(elapsed_seconds=110.0, remaining_output_tokens=0) == "wall_clock"
    )


def test_the_forced_submission_nudge_asks_for_the_turn_shape_the_prompt_defined() -> (
    None
):
    # The nudge is the last thing the model reads before its final turn, and the
    # MBPP prompt allows exactly one fenced code block per turn and nothing
    # else. A nudge written as prose invites a prose reply, or one more round of
    # debugging: task 260 answered a prose nudge with more code, and the run
    # ended with no answer at all rather than with its best attempt.
    assert "```python" in FORCED_SUBMISSION_NUDGE
    assert "final_answer" in FORCED_SUBMISSION_NUDGE
    assert "<end_code>" in FORCED_SUBMISSION_NUDGE


def test_the_nudge_example_is_a_call_the_model_can_copy_verbatim() -> None:
    # A pseudo-placeholder inside the triple quotes is something the model has
    # to substitute while it is out of budget and on its last turn. Task 260
    # mis-nested exactly that, sending `final_answer \'\'\'(` instead of a call,
    # and the run ended with no answer at all. The example is a real call
    # instead, the same shape the MBPP prompt already shows for a final turn.
    block = FORCED_SUBMISSION_NUDGE.split("```python")[1].split("```")[0]

    ast.parse(block)
    assert "<" not in block
