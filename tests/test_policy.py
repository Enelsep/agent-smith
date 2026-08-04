from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

if TYPE_CHECKING:
    from collections.abc import Iterator

SCRATCH = "/tmp/agent"


@pytest.fixture(scope="module")
def sb() -> Iterator[Sandbox]:
    with Sandbox(timeout=5.0) as sandbox:
        yield sandbox


# --- builtins allowlist ----------------------------------------------------

REMOVED_BUILTINS = [
    "eval",
    "exec",
    "compile",
    "input",
    "breakpoint",
    "help",
    "vars",
]


@pytest.mark.parametrize("name", REMOVED_BUILTINS)
def test_dangerous_builtins_are_absent(sb: Sandbox, name: str) -> None:
    # scan_for_escapes catches some by name; whatever slips through must still
    # be missing from the namespace rather than merely discouraged.
    r = sb.execute(f"print({name})")
    assert r.outcome in {Outcome.BLOCKED, Outcome.ERROR}
    if r.outcome is Outcome.ERROR:
        assert r.error is not None
        assert "NameError" in r.error


def test_class_definition_still_works(sb: Sandbox) -> None:
    # `class X:` compiles to __build_class__; dropping it breaks every solution
    # that defines a class, which is easy to miss until it happens.
    r = sb.execute(
        "class P:\n    def __init__(self):\n        self.v = 3\nprint(P().v)"
    )
    assert r.outcome is Outcome.OK
    assert r.stdout.strip() == "3"


def test_exception_classes_are_available(sb: Sandbox) -> None:
    r = sb.execute(
        "try:\n    {}['missing']\nexcept KeyError:\n    print('caught KeyError')"
    )
    assert r.outcome is Outcome.OK
    assert "caught KeyError" in r.stdout


def test_comprehensions_and_common_builtins_work(sb: Sandbox) -> None:
    r = sb.execute(
        "print(sorted({x % 3 for x in range(10)}), sum(range(5)), max([1, 9]))"
    )
    assert r.outcome is Outcome.OK
    assert r.stdout.strip() == "[0, 1, 2] 10 9"


# --- filesystem policy -----------------------------------------------------


def test_write_inside_allowed_directory_succeeds(sb: Sandbox) -> None:
    target = f"{SCRATCH}/hello.txt"
    r = sb.execute(
        f"with open({target!r}, 'w') as fh:\n"
        f"    fh.write('written by the sandbox')\n"
        f"with open({target!r}) as fh:\n"
        f"    print(fh.read())"
    )
    assert r.outcome is Outcome.OK
    assert "written by the sandbox" in r.stdout


def test_read_outside_allowlist_is_blocked(sb: Sandbox) -> None:
    r = sb.execute("print(open('/etc/passwd').read())")
    assert r.outcome is Outcome.BLOCKED
    assert r.error is not None
    assert "not permitted to read" in r.error
    assert "/etc/passwd" in r.error


def test_write_outside_allowlist_is_blocked(sb: Sandbox) -> None:
    r = sb.execute("open('/etc/evil.txt', 'w').write('x')")
    assert r.outcome is Outcome.BLOCKED
    assert r.error is not None
    assert "not permitted to write to" in r.error


def test_traversal_out_of_allowed_directory_is_blocked(sb: Sandbox) -> None:
    # Containment is checked on the realpath, so ../ cannot walk out.
    r = sb.execute(f"open({SCRATCH + '/../../etc/passwd'!r}).read()")
    assert r.outcome is Outcome.BLOCKED


def test_symlink_out_of_allowed_directory_is_blocked(sb: Sandbox) -> None:
    link = f"{SCRATCH}/escape_link"
    if not Path(link).is_symlink():
        Path(link).symlink_to("/etc/passwd")
    r = sb.execute(f"print(open({link!r}).read()[:10])")
    assert r.outcome is Outcome.BLOCKED


def test_imports_still_work_under_the_policy(sb: Sandbox) -> None:
    # Regression: importing a pure-Python stdlib module opens files under the
    # interpreter tree. If those reads are denied, every import breaks.
    r = sb.execute("import json\nimport collections.abc\nprint(json.dumps({'k': 1}))")
    assert r.outcome is Outcome.OK
    assert '{"k": 1}' in r.stdout


def test_missing_allowed_directory_does_not_break_startup() -> None:
    with Sandbox(timeout=5.0, allowed_directories=["/definitely/not/here"]) as sb:
        assert sb.execute("print(1 + 1)").outcome is Outcome.OK


# --- network and process bans (defence in depth) ---------------------------
# These deliberately allowlist the import so the *audit hook* is what does the
# blocking. In production the import guard refuses first; this proves the
# second layer holds if the first is ever bypassed.


def test_network_is_blocked_even_when_socket_is_importable() -> None:
    with Sandbox(timeout=5.0, authorized_imports=["socket"]) as sb:
        r = sb.execute("import socket\ns = socket.socket()")
        assert r.outcome is Outcome.BLOCKED
        assert r.error is not None
        assert "network access is disabled" in r.error


def test_subprocess_is_blocked_even_when_os_is_importable() -> None:
    with Sandbox(timeout=5.0, authorized_imports=["os", "os.*"]) as sb:
        r = sb.execute("import os\nos.system('echo pwned')")
        assert r.outcome is Outcome.BLOCKED
        assert r.error is not None
        assert "starting processes is disabled" in r.error


def test_os_open_bypass_is_blocked_by_the_audit_hook() -> None:
    # Removing builtins.open is not enough: os.open reaches the same syscall.
    with Sandbox(timeout=5.0, authorized_imports=["os", "os.*"]) as sb:
        r = sb.execute("import os\nfd = os.open('/etc/passwd', os.O_RDONLY)")
        assert r.outcome is Outcome.BLOCKED
        assert r.error is not None
        assert "not permitted to read" in r.error


def test_os_remove_outside_allowlist_is_blocked() -> None:
    with Sandbox(timeout=5.0, authorized_imports=["os", "os.*"]) as sb:
        r = sb.execute("import os\nos.remove('/etc/hostname')")
        assert r.outcome is Outcome.BLOCKED
        assert r.error is not None
        assert "not permitted to write to" in r.error


# --- memory limit ----------------------------------------------------------


def test_memory_limit_is_enforced_and_reported() -> None:
    with Sandbox(timeout=15.0, max_memory_mb=256) as sb:
        r = sb.execute("blob = bytearray(400 * 1024 * 1024)")
        assert r.outcome is Outcome.MEMORY_LIMIT
        assert r.error is not None
        assert "memory limit" in r.error


def test_sandbox_survives_a_memory_denial() -> None:
    with Sandbox(timeout=15.0, max_memory_mb=256) as sb:
        sb.execute("marker = 'still here'")
        sb.execute("blob = bytearray(400 * 1024 * 1024)")
        r = sb.execute("print(marker)")
        assert r.outcome is Outcome.OK
        assert r.stdout.strip() == "still here"
        assert sb.restarts == 0
