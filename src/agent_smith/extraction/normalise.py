"""Rewriting a decoded tool call as Python the sandbox can run."""

import json
import keyword
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a JSON value")


def decode_json(text: str) -> Any:
    """Decode JSON, refusing Python's non-standard constants."""
    return json.loads(text, parse_constant=_reject_constant)


def coerce_text_value(raw: str) -> Any:
    """Give an untyped XML parameter body its JSON type back, when it has one."""
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
