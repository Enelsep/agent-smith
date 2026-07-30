"""Each strategy on its own, without walking the chain."""

from agent_smith.extraction.result import Strategy
from agent_smith.extraction.strategies import STRATEGY_CHAIN, bare, fenced


def test_a_tagged_fence_yields_its_body() -> None:
    candidate = fenced("Here:\n```python\nresult = 1\n```\nDone.")

    assert candidate is not None
    assert candidate.code == "result = 1"
    assert candidate.repair_note is None


def test_an_untagged_fence_works_too() -> None:
    candidate = fenced("```\nresult = 1\n```")

    assert candidate is not None
    assert candidate.code == "result = 1"


def test_end_code_closes_a_fence() -> None:
    candidate = fenced("```python\nresult = 1\n<end_code>")

    assert candidate is not None
    assert candidate.code == "result = 1"


def test_an_unclosed_fence_takes_the_rest_and_says_so() -> None:
    # A matching problem, not a parsing one: without this the block would simply
    # fail to match, and a perfectly good body would be thrown away.
    candidate = fenced("```python\nresult = 1\n")

    assert candidate is not None
    assert candidate.code == "result = 1"
    assert candidate.repair_note is not None


def test_the_first_fence_wins() -> None:
    candidate = fenced("```python\nfirst = 1\n```\n```python\nsecond = 2\n```")

    assert candidate is not None
    assert candidate.code == "first = 1"


def test_no_fence_is_no_match() -> None:
    assert fenced("just prose") is None


def test_bare_python_is_taken_whole() -> None:
    candidate = bare("result = 1 + 1\nprint(result)")

    assert candidate is not None
    assert candidate.code == "result = 1 + 1\nprint(result)"


def test_prose_is_not_bare_python() -> None:
    assert bare("The answer is 42, I think.") is None


def test_a_one_word_reply_is_not_code() -> None:
    # `Yes` parses perfectly well as a Name expression. Without the
    # actionable-node test it would be shipped to the sandbox to earn a
    # pointless NameError.
    assert bare("Yes") is None


def test_a_bare_number_is_not_code() -> None:
    assert bare("42") is None


def test_an_import_alone_is_code() -> None:
    assert bare("import math") is not None


def test_a_function_definition_alone_is_code() -> None:
    assert bare("def f():\n    return 1") is not None


def test_an_empty_message_is_no_match() -> None:
    assert bare("   \n  ") is None


def test_the_chain_starts_with_fenced_and_ends_with_bare() -> None:
    strategies = [strategy for strategy, _ in STRATEGY_CHAIN]

    assert strategies[0] is Strategy.FENCED
    assert strategies[-1] is Strategy.BARE
