"""Pure functions deciding when the next LLM call would blow the task budget."""

from agent_smith.agent.budget import (
    MIN_OUTPUT_RESERVE,
    MIN_VIABLE_OUTPUT_TOKENS,
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
        max_input_tokens=max_input_tokens,
        elapsed_seconds=elapsed_seconds,
        max_wall_clock_seconds=max_wall_clock_seconds,
        remaining_output_tokens=remaining_output_tokens,
        reserved_output_tokens=reserved_output_tokens,
    )


def test_nothing_is_forced_while_every_ceiling_is_far_off() -> None:
    assert force_reason() is None


def test_the_input_guard_reserves_the_forced_call_it_will_ask_for() -> None:
    # 4700 + 250 is under the 15% margin of 5100, so counting the next
    # request once would let this iteration through — and the forced call
    # that follows it would then be spent outside any budget the guard
    # ever checked. Counting it twice is what reserves that call.
    assert 4700 + 250 < 5100
    assert force_reason(total_input_tokens=4700, estimated_next_input=250) == (
        "input_tokens"
    )


def test_the_input_guard_leaves_room_for_two_more_requests() -> None:
    assert force_reason(total_input_tokens=4000, estimated_next_input=250) is None


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
