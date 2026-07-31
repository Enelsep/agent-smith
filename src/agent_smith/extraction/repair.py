"""The one repair attempt.

The card asks for a single repair on a malformation, reported back to the model.
Read "single" as "we do not loop with the LLM", not as "we try one substitution":
each entry point walks a short ordered list of fixes and keeps the first that
makes the text valid. From the caller's side that is one attempt, one note.
"""

import ast
import re
from collections.abc import Callable, Sequence

from agent_smith.extraction.normalise import decode_json

_Fix = tuple[str, Callable[[str], str]]

# How far into a message we are willing to look for the start of the code. Past
# this, the text is prose with a snippet in it, not code with a preamble.
_MAX_PROSE_LINES = 5

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")

_CLOSERS = {"{": "}", "[": "]", "(": ")"}


def _decodes(text: str) -> bool:
    try:
        decode_json(text)
    except ValueError:
        return False
    return True


def _parses(text: str) -> bool:
    try:
        ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    return True


def _first_fix_that_works(
    text: str, fixes: Sequence[_Fix], is_valid: Callable[[str], bool]
) -> tuple[str, str] | None:
    for note, fix in fixes:
        candidate = fix(text)
        if candidate != text and is_valid(candidate):
            return candidate, note
    return None


def _strip_fence(text: str) -> str:
    match = _FENCE.match(text)
    return match.group("body") if match else text


def _drop_trailing_commas(text: str) -> str:
    return _TRAILING_COMMA.sub(r"\1", text)


def _close_structures(text: str, quotes: tuple[str, ...]) -> str:
    """Close an unterminated string and any still-open brackets, in one pass.

    Both come from the same cause — a generation that stopped mid-way — and they
    arrive together often enough that fixing them separately would leave the
    common case unrepaired. A truncated `print("hello` needs its quote *and* its
    parenthesis; either alone still will not parse.
    """
    stack: list[str] = []
    open_quote: str | None = None
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif open_quote is not None:
            if char == open_quote:
                open_quote = None
        elif char in quotes:
            open_quote = char
        elif char in _CLOSERS:
            stack.append(_CLOSERS[char])
        elif stack and char == stack[-1]:
            stack.pop()
    return text + (open_quote or "") + "".join(reversed(stack))


def _close_json_structures(text: str) -> str:
    return _close_structures(text, ('"',))


def _close_json_structures_after_a_comma(text: str) -> str:
    """Close the structures, then drop the comma closing them just exposed.

    A generation cut between two members ends on its separator — `{"a": 1,` —
    and closing that yields `{"a": 1,}`, which no more decodes than the original.
    The comma only becomes trailing once the brace is there, so the two fixes
    have to run in this order and in one pass.
    """
    return _drop_trailing_commas(_close_json_structures(text))


def _close_python_structures(code: str) -> str:
    return _close_structures(code, ('"', "'"))


def _drop_leading_prose(code: str) -> str:
    lines = code.splitlines()
    for count in range(1, min(_MAX_PROSE_LINES, len(lines)) + 1):
        candidate = "\n".join(lines[count:])
        # A blank candidate parses and says nothing. Dropping every line is not
        # a repair, it is a way of turning a bad reply into an empty one.
        if candidate.strip() and _parses(candidate):
            return candidate
    return code


def _drop_trailing_prose(code: str) -> str:
    lines = code.splitlines()
    for count in range(1, min(_MAX_PROSE_LINES, len(lines)) + 1):
        candidate = "\n".join(lines[:-count])
        if candidate.strip() and _parses(candidate):
            return candidate
    return code


_JSON_FIXES: tuple[_Fix, ...] = (
    ("stripped a markdown fence wrapped around the JSON", _strip_fence),
    ("dropped a trailing comma", _drop_trailing_commas),
    ("closed an unterminated string or object", _close_json_structures),
    # Last, so that the two notes above keep the cases they already describe on
    # their own and this one is only reported when it is the accurate account.
    (
        "closed an unterminated string or object and dropped the trailing comma",
        _close_json_structures_after_a_comma,
    ),
)

_PYTHON_FIXES: tuple[_Fix, ...] = (
    ("dropped prose before the code", _drop_leading_prose),
    ("dropped prose after the code", _drop_trailing_prose),
    ("closed an unterminated string or bracket", _close_python_structures),
)


def repair_json(payload: str) -> tuple[str, str] | None:
    """Try to make a tool-call payload decode. `(text, note)`, or `None`."""
    if _decodes(payload):
        return None
    return _first_fix_that_works(payload, _JSON_FIXES, _decodes)


def repair_python(code: str) -> tuple[str, str] | None:
    """Try to make a candidate parse. `(code, note)`, or `None`."""
    if _parses(code):
        return None
    return _first_fix_that_works(code, _PYTHON_FIXES, _parses)
