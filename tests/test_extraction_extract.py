"""The chain walk: what wins, what fails, and what it tells the model."""

import pytest

from agent_smith.extraction import ExtractionResult, Strategy, extract_code


def test_a_fenced_block_is_extracted() -> None:
    result = extract_code("Sure:\n```python\nresult = 1\n```")

    assert result.code == "result = 1"
    assert result.strategy is Strategy.FENCED
    assert result.failure is None


def test_the_higher_strategy_wins_when_two_markers_are_present() -> None:
    text = '```python\nresult = 1\n```\n<tool_call>{"name": "f"}</tool_call>'

    assert extract_code(text).strategy is Strategy.FENCED


def test_bare_python_is_the_last_resort() -> None:
    result = extract_code("result = 1 + 1")

    assert result.strategy is Strategy.BARE
    assert result.code == "result = 1 + 1"


def test_a_repair_is_reported() -> None:
    result = extract_code("```python\nresult = 1\n")

    assert result.code == "result = 1"
    assert result.repaired is True
    assert result.repair_note is not None


def test_prose_before_unfenced_code_is_repaired() -> None:
    result = extract_code("Sure, here you go:\nresult = 1 + 1")

    assert result.code is not None
    assert result.repaired is True


def test_a_broken_fence_is_not_rescued_by_the_bare_strategy() -> None:
    # BARE is for "no marker at all", not for "a marker that went wrong". The
    # fence matched, so the fence owns the outcome.
    result = extract_code("```python\ndef (((:\n```")

    assert result.code is None
    assert result.strategy is Strategy.FENCED


def test_an_already_repaired_candidate_does_not_get_a_second_repair() -> None:
    # One repair per extraction. The unclosed fence spent it; the unparseable
    # body does not get another.
    result = extract_code("```python\ndef (((:\n")

    assert result.code is None
    assert result.strategy is Strategy.FENCED


def test_a_malformed_tool_call_names_its_strategy() -> None:
    result = extract_code("<tool_call>not json at all {{{</tool_call>")

    assert result.code is None
    assert result.strategy is Strategy.HERMES
    assert result.failure


def test_prose_alone_matches_nothing() -> None:
    result = extract_code("I think the answer is 42.")

    assert result.code is None
    assert result.strategy is None


def test_the_failure_names_the_formats_that_would_have_worked() -> None:
    failure = extract_code("I think the answer is 42.").failure

    assert failure is not None
    for expected in ["```python", "<invoke", "<tool_call>", "Action:"]:
        assert expected in failure


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "   \n\t ",
        "\x00\x01\x02binary\xff",
        '<tool_call>{"name": "f", "arguments":</tool_call>',
        "```",
        "```python",
        "<invoke name=",
        "Action Input: {}",
        "}" * 5000,
        "```python\n" + "(" * 2000,
    ],
)
def test_extract_code_never_raises(junk: str) -> None:
    # CORE-4 must never raise, because a crash scores as an automatic fail. The
    # cheapest way to honour that is for what it calls to return values.
    assert isinstance(extract_code(junk), ExtractionResult)


def test_a_result_always_has_exactly_one_of_code_or_failure() -> None:
    for text in ["result = 1", "prose only", "<tool_call>{{{</tool_call>"]:
        result = extract_code(text)
        assert (result.code is None) != (result.failure is None)
