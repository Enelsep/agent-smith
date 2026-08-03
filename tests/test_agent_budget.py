"""Pure functions deciding when the next LLM call would blow the task budget."""

from agent_smith.agent.budget import (
    capped_max_tokens,
    remaining_output_tokens,
    estimate_tokens,
    should_force_submission,
)


def test_estimate_tokens_uses_a_four_chars_per_token_ratio() -> None:
    messages = [{"role": "user", "content": "a" * 40}]
    assert estimate_tokens(messages) == 10


def test_estimate_tokens_sums_every_message_in_the_list() -> None:
    messages = [
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


def test_should_force_submission_trips_on_input_tokens() -> None:
    reason = should_force_submission(
        total_input_tokens=5900,
        estimated_next_input=200,
        max_input_tokens=6000,
        elapsed_seconds=0.0,
        max_wall_clock_seconds=120.0,
        remaining_output_tokens=100,
    )
    assert reason == "input_tokens"


def test_should_force_submission_trips_on_wall_clock_margin() -> None:
    reason = should_force_submission(
        total_input_tokens=0,
        estimated_next_input=0,
        max_input_tokens=6000,
        elapsed_seconds=110.0,
        max_wall_clock_seconds=120.0,
        remaining_output_tokens=100,
    )
    assert reason == "wall_clock"


def test_should_force_submission_does_not_trip_just_under_the_wall_clock_margin() -> None:
    reason = should_force_submission(
        total_input_tokens=0,
        estimated_next_input=0,
        max_input_tokens=6000,
        elapsed_seconds=100.0,
        max_wall_clock_seconds=120.0,
        remaining_output_tokens=100,
    )
    assert reason is None


def test_should_force_submission_trips_on_the_output_floor() -> None:
    reason = should_force_submission(
        total_input_tokens=0,
        estimated_next_input=0,
        max_input_tokens=6000,
        elapsed_seconds=0.0,
        max_wall_clock_seconds=120.0,
        remaining_output_tokens=10,
    )
    assert reason == "output_tokens"


def test_should_force_submission_returns_none_when_nothing_is_close() -> None:
    reason = should_force_submission(
        total_input_tokens=100,
        estimated_next_input=100,
        max_input_tokens=6000,
        elapsed_seconds=10.0,
        max_wall_clock_seconds=120.0,
        remaining_output_tokens=100,
    )
    assert reason is None


def test_should_force_submission_prioritises_input_tokens_over_wall_clock() -> None:
    # Both would trip on their own; input_tokens is checked first and wins.
    reason = should_force_submission(
        total_input_tokens=5900,
        estimated_next_input=200,
        max_input_tokens=6000,
        elapsed_seconds=110.0,
        max_wall_clock_seconds=120.0,
        remaining_output_tokens=100,
    )
    assert reason == "input_tokens"
