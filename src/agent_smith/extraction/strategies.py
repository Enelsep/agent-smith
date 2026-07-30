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
from collections.abc import Callable
from dataclasses import dataclass

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


def fenced(text: str) -> Candidate | None:
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


def bare(text: str) -> Candidate | None:
    """Last resort: the whole message, if it is executable Python.

    "The model forgot the fence" is one of the commonest malformations and every
    marker-based strategy misses it. The actionable-node test is what keeps this
    honest — prose almost never parses, but a one-word reply does.
    """
    code = text.strip()
    if not code:
        return None
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    if not any(isinstance(node, _ACTIONABLE) for node in ast.walk(tree)):
        return None
    return Candidate(code)


STRATEGY_CHAIN: tuple[tuple[Strategy, Callable[[str], Candidate | None]], ...] = (
    (Strategy.FENCED, fenced),
    (Strategy.BARE, bare),
)
