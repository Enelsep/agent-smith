from __future__ import annotations

from collections.abc import Iterator

import pytest

from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

# (code, label, expected_outcome). Mixes real escape shapes that must be
# blocked with legitimate __class__ uses that must NOT be false-positived.
CASES = [
    (
        "getattr(().__class__, '__subclasses__')()",
        "getattr with literal dunder",
        Outcome.BLOCKED,
    ),
    (
        "().__class__.__base__.__subclasses__()",
        "direct chain",
        Outcome.BLOCKED,
    ),
    (
        "x = ().__class__\nprint(x)",
        "harmless __class__ alone",
        Outcome.OK,
    ),
    (
        (
            "class A:\n    def f(self):\n"
            "        return self.__class__.__name__\nprint(A().f())"
        ),
        "legit __class__ use (false-positive check)",
        Outcome.OK,
    ),
    (
        # Attribute name is computed, but the __mro__ access on the same line
        # is a static hit, and _guarded_getattr catches the resolved name too.
        "m = '__subcl' + 'asses__'\nprint(len(getattr(().__class__.__mro__[-1], m)()))",
        "computed attribute name",
        Outcome.BLOCKED,
    ),
]


@pytest.fixture(scope="module")
def sb() -> Iterator[Sandbox]:
    with Sandbox(timeout=5.0) as sandbox:
        yield sandbox


@pytest.mark.parametrize(
    "code,expected",
    [(c[0], c[2]) for c in CASES],
    ids=[c[1] for c in CASES],
)
def test_escape_outcomes(sb: Sandbox, code: str, expected: Outcome) -> None:
    assert sb.execute(code).outcome is expected
