"""Decoding a tool call and writing it back out as Python."""

import ast

import pytest

from agent_smith.extraction.normalise import (
    coerce_text_value,
    decode_json,
    render_calls,
)


def test_decoding_ordinary_json_works() -> None:
    assert decode_json('{"a": 1}') == {"a": 1}


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_pythons_json_extensions_are_rejected(constant: str) -> None:
    # json.loads accepts these by default, and repr(float("nan")) is `nan` — a
    # bare name that resolves to nothing in the sandbox. Failing here turns a
    # NameError three layers later into an ordinary decode failure.
    with pytest.raises(ValueError):
        decode_json(f'{{"x": {constant}}}')


def test_malformed_json_raises_value_error() -> None:
    with pytest.raises(ValueError):
        decode_json('{"a": ')


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12", 12),
        ("1.5", 1.5),
        ("true", True),
        ("false", False),
        ("null", None),
        ("[1, 2]", [1, 2]),
        ("/tmp/a.py", "/tmp/a.py"),
        ("", ""),
        ("hello world", "hello world"),
    ],
)
def test_xml_values_recover_their_json_type(raw: str, expected: object) -> None:
    assert coerce_text_value(raw) == expected


def test_an_explicitly_quoted_number_stays_a_string() -> None:
    # decode_json('"12"') yields the str "12". Keeping a decoded str would strip
    # the quotes the model deliberately wrote, so decoded strings are discarded.
    assert coerce_text_value('"12"') == '"12"'


def test_nan_in_an_xml_value_stays_text() -> None:
    assert coerce_text_value("NaN") == "NaN"


def test_one_call_renders_as_an_assign_and_a_print() -> None:
    rendered = render_calls([("read_file", {"filepath": "/tmp/a.py"})], 1)

    assert rendered == (
        "result_1_1 = read_file(filepath='/tmp/a.py')\nprint(result_1_1)"
    )


def test_values_are_rendered_as_python_literals() -> None:
    rendered = render_calls(
        [("read_file", {"filepath": "/tmp/a.py", "start_line": 12, "end_line": None})],
        1,
    )

    assert "start_line=12" in rendered
    assert "end_line=None" in rendered
    assert "start_line='12'" not in rendered


@pytest.mark.parametrize(
    "value",
    ["O'Brien", 'He said "hi"', "back\\slash", "line\nbreak", [1, {"a": None}], True],
)
def test_every_rendered_value_parses_back_to_itself(value: object) -> None:
    rendered = render_calls([("f", {"x": value})], 1)
    statement = ast.parse(rendered).body[0]

    assert isinstance(statement, ast.Assign)
    call = statement.value
    assert isinstance(call, ast.Call)
    assert ast.literal_eval(call.keywords[0].value) == value


def test_several_calls_are_numbered_in_order() -> None:
    rendered = render_calls([("first", {}), ("second", {})], 1)

    assert rendered == (
        "result_1_1 = first()\nprint(result_1_1)\n"
        "result_1_2 = second()\nprint(result_1_2)"
    )


def test_no_calls_render_to_nothing() -> None:
    assert render_calls([], 1) == ""


def test_the_step_is_part_of_the_variable_name() -> None:
    # The worker builds the namespace once and reuses it, so a name restarting
    # at result_1 every step would overwrite the previous step's value in place.
    first = render_calls([("f", {})], 1)
    third = render_calls([("f", {})], 3)

    assert "result_1_1" in first
    assert "result_3_1" in third
    assert first != third
