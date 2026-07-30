"""Each strategy on its own, without walking the chain."""

import pytest

from agent_smith.extraction.result import Strategy
from agent_smith.extraction.strategies import (
    STRATEGY_CHAIN,
    PayloadError,
    bare,
    fenced,
    hermes,
    react,
    xml,
)


def test_a_tagged_fence_yields_its_body() -> None:
    candidate = fenced("Here:\n```python\nresult = 1\n```\nDone.", 1)

    assert candidate is not None
    assert candidate.code == "result = 1"
    assert candidate.repair_note is None


def test_an_untagged_fence_works_too() -> None:
    candidate = fenced("```\nresult = 1\n```", 1)

    assert candidate is not None
    assert candidate.code == "result = 1"


def test_end_code_closes_a_fence() -> None:
    candidate = fenced("```python\nresult = 1\n<end_code>", 1)

    assert candidate is not None
    assert candidate.code == "result = 1"


def test_an_unclosed_fence_takes_the_rest_and_says_so() -> None:
    # A matching problem, not a parsing one: without this the block would simply
    # fail to match, and a perfectly good body would be thrown away.
    candidate = fenced("```python\nresult = 1\n", 1)

    assert candidate is not None
    assert candidate.code == "result = 1"
    assert candidate.repair_note is not None


def test_the_first_fence_wins() -> None:
    candidate = fenced("```python\nfirst = 1\n```\n```python\nsecond = 2\n```", 1)

    assert candidate is not None
    assert candidate.code == "first = 1"


def test_no_fence_is_no_match() -> None:
    assert fenced("just prose", 1) is None


def test_bare_python_is_taken_whole() -> None:
    candidate = bare("result = 1 + 1\nprint(result)", 1)

    assert candidate is not None
    assert candidate.code == "result = 1 + 1\nprint(result)"


def test_prose_is_not_bare_python() -> None:
    assert bare("The answer is 42, I think.", 1) is None


def test_a_one_word_reply_is_not_code() -> None:
    # `Yes` parses perfectly well as a Name expression. Without the
    # actionable-node test it would be shipped to the sandbox to earn a
    # pointless NameError.
    assert bare("Yes", 1) is None


def test_a_bare_number_is_not_code() -> None:
    assert bare("42", 1) is None


def test_an_import_alone_is_code() -> None:
    assert bare("import math", 1) is not None


def test_a_function_definition_alone_is_code() -> None:
    assert bare("def f():\n    return 1", 1) is not None


def test_an_empty_message_is_no_match() -> None:
    assert bare("   \n  ", 1) is None


def test_bare_python_behind_a_preamble_is_repaired_here() -> None:
    # No marker delimits the code, so "where does the Python start" is this
    # strategy's own question. Refusing this would cost an iteration to ask again.
    candidate = bare("Sure, here you go:\nresult = 1 + 1", 1)

    assert candidate is not None
    assert candidate.code == "result = 1 + 1"
    assert candidate.repair_note is not None


def test_a_preamble_over_prose_is_still_no_match() -> None:
    # The repair must not turn a prose reply into code by dropping lines until
    # something parses.
    assert bare("Let me think about this.\nIt is probably 42.", 1) is None


def test_an_invoke_block_becomes_a_call() -> None:
    candidate = xml(
        '<invoke name="read_file">'
        '<parameter name="filepath">/tmp/a.py</parameter>'
        "</invoke>",
        1,
    )

    assert candidate is not None
    assert (
        candidate.code
        == "result_1_1 = read_file(filepath='/tmp/a.py')\nprint(result_1_1)"
    )


def test_xml_numeric_parameters_are_not_strings() -> None:
    # XML carries no types. Rendering start_line as '12' would hand TOOL-1 a
    # string where it wants an int.
    candidate = xml(
        '<invoke name="read_file">'
        '<parameter name="filepath">/tmp/a.py</parameter>'
        '<parameter name="start_line">12</parameter>'
        "</invoke>",
        1,
    )

    assert candidate is not None
    assert "start_line=12" in candidate.code


def test_several_invoke_blocks_are_all_kept() -> None:
    candidate = xml('<invoke name="first"></invoke><invoke name="second"></invoke>', 1)

    assert candidate is not None
    assert "result_1_1 = first()" in candidate.code
    assert "result_1_2 = second()" in candidate.code


def test_no_invoke_block_is_no_match() -> None:
    assert xml("just prose", 1) is None


def test_a_tool_call_becomes_a_call() -> None:
    candidate = hermes(
        '<tool_call>{"name": "read_file", "arguments": {"filepath": "/tmp/a.py"}}</tool_call>',
        1,
    )

    assert candidate is not None
    assert (
        candidate.code
        == "result_1_1 = read_file(filepath='/tmp/a.py')\nprint(result_1_1)"
    )


def test_a_tool_call_without_arguments_is_fine() -> None:
    candidate = hermes('<tool_call>{"name": "get_patch"}</tool_call>', 1)

    assert candidate is not None
    assert "result_1_1 = get_patch()" in candidate.code


def test_several_tool_calls_are_all_kept() -> None:
    candidate = hermes(
        '<tool_call>{"name": "first"}</tool_call>'
        '<tool_call>{"name": "second"}</tool_call>',
        1,
    )

    assert candidate is not None
    assert "result_1_2 = second()" in candidate.code


def test_a_repairable_tool_call_reports_the_repair() -> None:
    candidate = hermes('<tool_call>{"name": "get_patch",}</tool_call>', 1)

    assert candidate is not None
    assert candidate.repair_note is not None


def test_an_unrepairable_tool_call_raises_payload_error() -> None:
    with pytest.raises(PayloadError):
        hermes("<tool_call>not json at all {{{</tool_call>", 1)


def test_a_tool_call_without_a_name_raises_payload_error() -> None:
    with pytest.raises(PayloadError):
        hermes('<tool_call>{"arguments": {}}</tool_call>', 1)


def test_a_numeric_tool_name_raises_payload_error() -> None:
    with pytest.raises(PayloadError):
        hermes('<tool_call>{"name": 123}</tool_call>', 1)


def test_an_empty_tool_name_raises_payload_error() -> None:
    with pytest.raises(PayloadError):
        hermes('<tool_call>{"name": ""}</tool_call>', 1)


def test_non_object_arguments_raise_payload_error() -> None:
    with pytest.raises(PayloadError):
        hermes('<tool_call>{"name": "f", "arguments": [1, 2]}</tool_call>', 1)


def test_a_tool_call_that_is_not_an_object_raises_payload_error() -> None:
    with pytest.raises(PayloadError):
        hermes("<tool_call>[1, 2]</tool_call>", 1)


def test_extra_keys_in_a_tool_call_are_ignored() -> None:
    candidate = hermes('<tool_call>{"name": "get_patch", "id": "abc"}</tool_call>', 1)

    assert candidate is not None
    assert candidate.code == "result_1_1 = get_patch()\nprint(result_1_1)"


def test_react_rejects_a_non_object_action_input() -> None:
    # Same definition of a call as Hermes, reached through the shared model.
    with pytest.raises(PayloadError):
        react("Action: read_file\nAction Input: [1, 2]", 1)


def test_nan_in_a_tool_call_raises_payload_error() -> None:
    # repr(float("nan")) is `nan`, a bare name. Better a decode failure here
    # than a NameError in the sandbox.
    with pytest.raises(PayloadError):
        hermes('<tool_call>{"name": "f", "arguments": {"x": NaN}}</tool_call>', 1)


def test_no_tool_call_is_no_match() -> None:
    assert hermes("just prose", 1) is None


def test_a_react_pair_becomes_a_call() -> None:
    candidate = react(
        "Thought: I should look.\n"
        "Action: read_file\n"
        'Action Input: {"filepath": "/tmp/a.py"}',
        1,
    )

    assert candidate is not None
    assert (
        candidate.code
        == "result_1_1 = read_file(filepath='/tmp/a.py')\nprint(result_1_1)"
    )


def test_a_react_action_without_input_raises_payload_error() -> None:
    with pytest.raises(PayloadError):
        react("Action: read_file", 1)


def test_no_action_line_is_no_match() -> None:
    assert react("just prose", 1) is None


def test_the_chain_is_in_spec_order() -> None:
    assert [strategy for strategy, _ in STRATEGY_CHAIN] == [
        Strategy.FENCED,
        Strategy.XML,
        Strategy.HERMES,
        Strategy.REACT,
        Strategy.BARE,
    ]
