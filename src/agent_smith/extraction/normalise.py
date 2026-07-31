"""Rewriting a decoded tool call as Python the sandbox can run."""

import json
import keyword
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a JSON value")


def decode_json(text: str) -> Any:
    """Decode JSON, refusing Python's non-standard constants.

    `json.loads` accepts `NaN`, `Infinity` and `-Infinity` by default as an
    extension to the standard, and `repr(float("nan"))` is `nan` — a bare name
    that resolves to nothing in the sandbox. Rejecting them here turns what would
    be a `NameError` three layers later into a decode failure handled where it
    belongs.

    Raises `ValueError` on anything it will not decode; `json.JSONDecodeError` is
    a subclass, so one `except ValueError` covers both.
    """
    return json.loads(text, parse_constant=_reject_constant)


def coerce_text_value(raw: str) -> Any:
    """Give an untyped XML parameter body its JSON type back, when it has one.

    XML carries no type information, so `start_line` arrives as the text `12` and
    would be rendered as a string TOOL-1 cannot use. Decoding recovers ints,
    floats, booleans and null.

    The decoded value is kept only when it is not a `str`. That condition is what
    stops an explicitly quoted `"12"` from silently shedding its quotes.
    """
    try:
        value = decode_json(raw)
    except ValueError:
        return raw
    return raw if isinstance(value, str) else value


def render_calls(calls: Sequence[tuple[str, Mapping[str, Any]]], step: int) -> str:
    """One assign-then-print pair per call, in order of appearance.

    Assign *and* print, because each half alone loses something. A bare
    assignment prints nothing, and the worker reports stdout — the model would
    fire a tool call and observe silence. A bare print discards the value, when a
    namespace that persists across steps is the whole point of the sandbox.

    Which is exactly why the variable is named for its step. The worker builds
    the namespace once and reuses it for every execution, so a name restarting at
    `result_1` each step would overwrite the previous step's value in place —
    leaving a stale binding under a live name, with nothing to signal it.
    `result_3_1` cannot collide with an earlier step.

    Values go through `repr()`, which yields a correct Python literal for every
    type JSON can produce and picks its own quoting. String interpolation would
    turn `12` into `"12"`, `None` into the four-character string `"None"`, and a
    value containing a double quote into a `SyntaxError`.

    Argument names get the same care. JSON and XML both admit names Python
    cannot spell as a keyword argument — `start-line`, `class`, `2nd` — and
    `f(start-line=1)` is a `SyntaxError`, which the caller would read as "the
    model sent bad code" when the payload decoded perfectly well. One such name
    sends the whole call through `**{...}`, where any string is a legal key. All
    of them or none of them, because a call cannot mix `**` expansion with
    keyword arguments that follow it.
    """
    lines: list[str] = []
    for index, (name, arguments) in enumerate(calls, start=1):
        variable = f"result_{step}_{index}"
        lines.append(f"{variable} = {name}({_render_arguments(arguments)})")
        lines.append(f"print({variable})")
    return "\n".join(lines)


def _render_arguments(arguments: Mapping[str, Any]) -> str:
    """The inside of the parentheses: keyword arguments, or one `**` mapping."""
    if all(_is_keyword_argument_name(key) for key in arguments):
        return ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    return f"**{dict(arguments)!r}"


def _is_keyword_argument_name(key: str) -> bool:
    """True when `key=` is something Python will parse.

    Soft keywords (`match`, `case`, `type`) are deliberately allowed: they are
    only keywords in the grammar positions that give them meaning, and
    `f(match=1)` is not one of them.
    """
    return key.isidentifier() and not keyword.iskeyword(key)
