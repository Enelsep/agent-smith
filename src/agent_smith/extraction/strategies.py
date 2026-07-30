"""Turning whatever the model wrote into a Python candidate.

A strategy has three possible outcomes, which is why it does not simply return a
string. `None` means the marker was absent. A `Candidate` means the marker was
there and produced code, possibly after a repair the strategy performed while
matching. `PayloadError` means the marker was there but its payload would not
decode even after repair — a distinction the caller needs, both to name the
strategy in the failure and to avoid spending a second repair on it.
"""

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent_smith.extraction.normalise import (
    coerce_text_value,
    decode_json,
    render_calls,
)
from agent_smith.extraction.repair import repair_json, repair_python
from agent_smith.extraction.result import Strategy

# The statement kinds that make a parsed tree worth executing. A module holding
# nothing but a bare Name or Constant parses fine and does nothing.
_ACTIONABLE = (
    ast.Call,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Return,
)

_FENCE_OPEN = re.compile(r"```[ \t]*(?:python|py)?[ \t]*\r?\n")
_FENCE_CLOSE = re.compile(r"```|<end_code>")
_INVOKE = re.compile(
    r"<invoke\s+name=[\"'](?P<name>[^\"']+)[\"']\s*>(?P<body>.*?)</invoke>", re.DOTALL
)
_PARAMETER = re.compile(
    r"<parameter\s+name=[\"'](?P<name>[^\"']+)[\"']\s*>(?P<value>.*?)</parameter>",
    re.DOTALL,
)
_TOOL_CALL = re.compile(r"<tool_call>(?P<payload>.*?)</tool_call>", re.DOTALL)
_ACTION = re.compile(r"^[ \t]*Action[ \t]*:[ \t]*(?P<name>\S+)[ \t]*$", re.MULTILINE)
_ACTION_INPUT = re.compile(
    r"^[ \t]*Action Input[ \t]*:[ \t]*(?P<payload>.+?)"
    r"(?=\n[ \t]*(?:Action|Thought|Observation)[ \t]*:|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class Candidate:
    """Code a strategy produced, and what it had to mend to get there."""

    code: str
    repair_note: str | None = None


class PayloadError(Exception):
    """A marker matched but its payload would not decode, repair included."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fenced(text: str, _step: int) -> Candidate | None:
    """A ``` block, optionally tagged, closed by ``` or by `<end_code>`.

    The first block wins: the CORE-6 prompt asks for one code block per turn, so
    in the nominal case "first" and "only" are the same block.
    """
    opening = _FENCE_OPEN.search(text)
    if opening is None:
        return None
    rest = text[opening.end() :]
    closing = _FENCE_CLOSE.search(rest)
    if closing is None:
        return Candidate(
            rest.strip(),
            "the code fence was never closed, so I took the rest of the message",
        )
    return Candidate(rest[: closing.start()].strip())


def _parsed(code: str) -> ast.Module | None:
    try:
        return ast.parse(code)
    except (SyntaxError, ValueError):
        return None


def bare(text: str, _step: int) -> Candidate | None:
    """Last resort: the whole message, if it is executable Python.

    "The model forgot the fence" is one of the commonest malformations and every
    marker-based strategy misses it. The actionable-node test is what keeps this
    honest — prose almost never parses, but a one-word reply does.

    A preamble is repaired here rather than by the caller. There is no marker to
    delimit the code, so "where does the Python start" is this strategy's own
    question — a reply of "Sure, here you go:" followed by a working line is a
    hit, not a miss, and refusing it would cost an iteration to ask again.
    """
    code = text.strip()
    if not code:
        return None
    note: str | None = None
    tree = _parsed(code)
    if tree is None:
        repaired = repair_python(code)
        if repaired is None:
            return None
        code, note = repaired
        tree = _parsed(code)
        if tree is None:
            return None
    if not any(isinstance(node, _ACTIONABLE) for node in ast.walk(tree)):
        return None
    return Candidate(code, note)


def _as_call(decoded: Any) -> tuple[str, Mapping[str, Any]]:
    """Read a decoded tool call, or say why it is not one."""
    if not isinstance(decoded, dict):
        raise PayloadError("a tool call must be a JSON object")
    name = decoded.get("name")
    if not isinstance(name, str) or not name:
        raise PayloadError('a tool call needs a string "name"')
    arguments = decoded.get("arguments", {})
    if not isinstance(arguments, dict):
        raise PayloadError('"arguments" must be a JSON object')
    return name, arguments


def _decode_payloads(payloads: Sequence[str]) -> tuple[list[Any], str | None]:
    """Decode every payload, spending at most one repair across all of them.

    The budget is one repair per extraction. A second malformed payload after a
    repair has already been spent is a failure, not another attempt.
    """
    decoded: list[Any] = []
    note: str | None = None
    for payload in payloads:
        try:
            decoded.append(decode_json(payload))
            continue
        except ValueError:
            pass
        if note is not None:
            raise PayloadError("more than one tool call payload would not decode")
        repaired = repair_json(payload)
        if repaired is None:
            raise PayloadError("the tool call payload would not decode")
        text, note = repaired
        try:
            decoded.append(decode_json(text))
        except ValueError:
            raise PayloadError("the tool call payload would not decode") from None
    return decoded, note


def xml(text: str, step: int) -> Candidate | None:
    """Anthropic-style `<invoke>` blocks with `<parameter>` bodies."""
    invocations = list(_INVOKE.finditer(text))
    if not invocations:
        return None
    calls = [
        (
            invocation.group("name"),
            {
                parameter.group("name"): coerce_text_value(parameter.group("value"))
                for parameter in _PARAMETER.finditer(invocation.group("body"))
            },
        )
        for invocation in invocations
    ]
    return Candidate(render_calls(calls, step))


def hermes(text: str, step: int) -> Candidate | None:
    """Hermes `<tool_call>` blocks holding a JSON object each."""
    payloads = [match.group("payload") for match in _TOOL_CALL.finditer(text)]
    if not payloads:
        return None
    decoded, note = _decode_payloads(payloads)
    return Candidate(render_calls([_as_call(item) for item in decoded], step), note)


def react(text: str, step: int) -> Candidate | None:
    """ReAct `Action:` / `Action Input:` pairs."""
    names = [match.group("name") for match in _ACTION.finditer(text)]
    if not names:
        return None
    payloads = [
        match.group("payload").strip() for match in _ACTION_INPUT.finditer(text)
    ]
    if len(payloads) != len(names):
        raise PayloadError("every Action: line needs its own Action Input: line")
    decoded, note = _decode_payloads(payloads)
    calls: list[tuple[str, Mapping[str, Any]]] = []
    for name, arguments in zip(names, decoded, strict=True):
        if not isinstance(arguments, dict):
            raise PayloadError("Action Input: must be a JSON object")
        calls.append((name, arguments))
    return Candidate(render_calls(calls, step), note)


STRATEGY_CHAIN: tuple[tuple[Strategy, Callable[[str, int], Candidate | None]], ...] = (
    (Strategy.FENCED, fenced),
    (Strategy.XML, xml),
    (Strategy.HERMES, hermes),
    (Strategy.REACT, react),
    (Strategy.BARE, bare),
)
