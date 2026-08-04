from __future__ import annotations

from collections.abc import Iterator

import pytest

from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome


# One worker for the whole module: the blocked/escape cases never execute, and
# the allowlisted imports only add harmless modules to the shared namespace.
@pytest.fixture(scope="module")
def sb() -> Iterator[Sandbox]:
    with Sandbox(timeout=5.0) as sandbox:
        yield sandbox


ALLOWED = [
    ("import math; print(math.sqrt(16))", "plain allowlisted import"),
    ("from collections import Counter; print(Counter('aab'))", "from-import"),
    (
        "import collections.abc; print(collections.abc.Sized is not None)",
        "wildcard submodule",
    ),
    ("from typing import List; print(List)", "typing"),
    ("import json; print(json.dumps({'a': 1}))", "json"),
    ("import re; print(re.findall(r'\\d+', 'a1b22'))", "re"),
]

BLOCKED_IMPORTS = [
    ("import os", "os"),
    ("import sys", "sys"),
    ("import subprocess", "subprocess"),
    ("import socket", "socket"),
    ("import importlib", "importlib"),
    ("import ctypes", "ctypes"),
    ("from os import path", "from os import"),
    ("import os.path", "dotted os"),
    ("__import__('os')", "__import__ by name"),
    ("import builtins", "builtins module"),
    ("import pickle", "pickle"),
]

ESCAPES = [
    ("().__class__.__base__.__subclasses__()", "subclasses walk"),
    ("[c for c in ().__class__.__mro__]", "mro walk"),
    ("print.__globals__", "func globals"),
    ("getattr(().__class__, '__subclasses__')", "getattr dunder"),
]

# These need no scan of their own: they are absent from the builtins allowlist,
# so reaching for one is already a NameError. See `scan_for_escapes`.
REMOVED_BUILTINS = [
    ("eval('1+1')", "eval"),
    ("exec('x=1')", "exec"),
    ("compile('1','<s>','eval')", "compile"),
    ("globals()", "globals"),
    ("vars()", "vars"),
    ("memoryview(b'x')", "memoryview"),
]


@pytest.mark.parametrize("code", [c[0] for c in ALLOWED], ids=[c[1] for c in ALLOWED])
def test_allowlisted_imports_run(sb: Sandbox, code: str) -> None:
    assert sb.execute(code).outcome is Outcome.OK


@pytest.mark.parametrize(
    "code", [c[0] for c in BLOCKED_IMPORTS], ids=[c[1] for c in BLOCKED_IMPORTS]
)
def test_off_allowlist_imports_are_blocked(sb: Sandbox, code: str) -> None:
    assert sb.execute(code).outcome is Outcome.BLOCKED


@pytest.mark.parametrize("code", [c[0] for c in ESCAPES], ids=[c[1] for c in ESCAPES])
def test_introspection_escapes_are_blocked(sb: Sandbox, code: str) -> None:
    assert sb.execute(code).outcome is Outcome.BLOCKED


@pytest.mark.parametrize(
    "code", [c[0] for c in REMOVED_BUILTINS], ids=[c[1] for c in REMOVED_BUILTINS]
)
def test_removed_builtins_raise_name_error(sb: Sandbox, code: str) -> None:
    r = sb.execute(code)
    assert r.outcome is Outcome.ERROR
    assert r.error is not None
    assert "NameError" in r.error


def test_final_answer_plain(sb: Sandbox) -> None:
    r = sb.execute("final_answer('plain')")
    assert r.outcome is Outcome.FINAL_ANSWER
    assert r.final_answer == "plain"


def test_final_answer_not_swallowed_by_except_exception(sb: Sandbox) -> None:
    r = sb.execute(
        "try:\n    final_answer('wrapped')\nexcept Exception:\n    print('SWALLOWED')\n"
    )
    assert r.outcome is Outcome.FINAL_ANSWER
    assert r.final_answer == "wrapped"
    assert "SWALLOWED" not in r.stdout


def test_final_answer_is_caught_by_except_baseexception(sb: Sandbox) -> None:
    # Documented limitation: an explicit `except BaseException` does swallow it.
    r = sb.execute(
        "try:\n"
        "    final_answer('broad')\n"
        "except BaseException:\n"
        "    print('SWALLOWED')\n"
    )
    assert r.final_answer is None
    assert "SWALLOWED" in r.stdout


def test_final_answer_from_nested_function(sb: Sandbox) -> None:
    r = sb.execute("def submit():\n    final_answer('from nested fn')\nsubmit()")
    assert r.outcome is Outcome.FINAL_ANSWER
    assert r.final_answer == "from nested fn"


def test_namespace_intact_after_blocked_and_escape_attempts(sb: Sandbox) -> None:
    r = sb.execute("import math\nprint('still working:', math.pi > 3)")
    assert r.outcome is Outcome.OK
    assert "still working: True" in r.stdout
    assert sb.restarts == 0
