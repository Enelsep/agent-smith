"""One repair attempt, at whichever level the malformation lives.

"One attempt" means we do not loop with the model, not that we try a single
substitution: each entry point runs a short ordered list of fixes and keeps the
first that makes the text valid.
"""

import ast

from agent_smith.extraction.normalise import decode_json
from agent_smith.extraction.repair import repair_json, repair_python


def test_a_markdown_fence_around_json_is_stripped() -> None:
    repaired = repair_json('```json\n{"name": "read_file"}\n```')

    assert repaired is not None
    text, note = repaired
    assert decode_json(text) == {"name": "read_file"}
    assert note


def test_a_trailing_comma_is_dropped() -> None:
    repaired = repair_json('{"a": 1,}')

    assert repaired is not None
    assert decode_json(repaired[0]) == {"a": 1}


def test_an_unterminated_string_is_closed() -> None:
    # A truncated generation, which stops mid-string and never gets to the
    # closing brace — not a stray brace inside the string.
    repaired = repair_json('{"name": "read_file')

    assert repaired is not None
    assert decode_json(repaired[0]) == {"name": "read_file"}


def test_an_unclosed_object_is_closed() -> None:
    repaired = repair_json('{"a": {"b": 1}')

    assert repaired is not None
    assert decode_json(repaired[0]) == {"a": {"b": 1}}


def test_a_truncation_that_stops_on_a_comma_is_repaired() -> None:
    # What max_tokens leaves behind when it cuts between two arguments. Closing
    # the object alone yields `{"code": "x",}`, which is still not JSON.
    repaired = repair_json('{"name": "run", "arguments": {"code": "x",')

    assert repaired is not None
    assert decode_json(repaired[0]) == {"name": "run", "arguments": {"code": "x"}}


def test_a_truncation_that_stops_inside_a_string_after_a_comma_is_repaired() -> None:
    repaired = repair_json('{"a": 1, "b": "half')

    assert repaired is not None
    assert decode_json(repaired[0]) == {"a": 1, "b": "half"}


def test_valid_json_is_not_repaired() -> None:
    # Nothing changed it, so there is no repair to report.
    assert repair_json('{"a": 1}') is None


def test_hopeless_json_is_not_repaired() -> None:
    assert repair_json("this was never JSON at all {{{") is None


def test_prose_before_the_code_is_dropped() -> None:
    repaired = repair_python("Sure, here you go:\n\nresult = 1 + 1\n")

    assert repaired is not None
    text, note = repaired
    ast.parse(text)
    assert note


def test_prose_after_the_code_is_dropped() -> None:
    repaired = repair_python("result = 1 + 1\n\nHope that helps!")

    assert repaired is not None
    ast.parse(repaired[0])


def test_an_unterminated_python_string_is_closed() -> None:
    repaired = repair_python('print("hello)')

    assert repaired is not None
    ast.parse(repaired[0])


def test_valid_python_is_not_repaired() -> None:
    assert repair_python("result = 1 + 1") is None


def test_hopeless_python_is_not_repaired() -> None:
    assert repair_python("def (((:") is None


def test_the_note_says_what_was_mended() -> None:
    # Reporting it is the point: a model told its fence was unterminated stops
    # producing unterminated fences. A silent fix teaches it nothing.
    repaired = repair_python("Sure!\nresult = 1")

    assert repaired is not None
    assert "prose" in repaired[1]
