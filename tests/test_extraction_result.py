"""The record one extraction attempt leaves behind."""

import pytest
from pydantic import ValidationError

from agent_smith.extraction import ExtractionResult, Strategy


def test_a_success_carries_code_and_no_failure() -> None:
    result = ExtractionResult(code="print(1)", strategy=Strategy.FENCED)

    assert result.code == "print(1)"
    assert result.failure is None
    assert result.repaired is False


def test_a_failure_still_names_the_strategy_that_matched() -> None:
    # The distinction the model needs: "your Hermes block was malformed" is
    # actionable, "nothing matched" is not.
    result = ExtractionResult(
        strategy=Strategy.HERMES, failure="the JSON would not decode"
    )

    assert result.code is None
    assert result.strategy is Strategy.HERMES


def test_nothing_matching_leaves_the_strategy_unset() -> None:
    result = ExtractionResult(failure="no code found")

    assert result.strategy is None


def test_code_and_failure_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult(code="print(1)", failure="also broken")


def test_one_of_code_or_failure_is_required() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult()


def test_the_result_is_frozen() -> None:
    result = ExtractionResult(code="print(1)", strategy=Strategy.BARE)

    with pytest.raises(ValidationError):
        result.code = "print(2)"


def test_the_strategy_values_are_their_names() -> None:
    # They land in metrics and in BENCH-3's ablation, so they are part of the
    # output format, not an implementation detail.
    assert [member.value for member in Strategy] == [
        "fenced",
        "xml",
        "hermes",
        "react",
        "bare",
    ]
