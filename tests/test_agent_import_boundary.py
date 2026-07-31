"""Importing the agent package starts no subprocess machinery."""

import subprocess
import sys


def test_importing_the_agent_package_does_not_load_multiprocessing() -> None:
    # The loop drives a sandbox but never builds one, so it types the sandbox
    # structurally and never names `agent_smith.sandbox.process`. That keeps
    # the fork/spawn machinery out of any process that only wants to read the
    # contract. Importing the concrete Sandbox would undo it silently.
    # A subprocess is the only honest probe: by the time pytest collects this
    # file, `multiprocessing` is long since in our own `sys.modules`.
    probe = "import sys, agent_smith.agent; print('multiprocessing' in sys.modules)"
    loaded = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert loaded == "False"


def test_the_sandbox_process_module_does_load_it() -> None:
    # Guards the test above against passing because the probe broke.
    probe = (
        "import sys, agent_smith.sandbox.process; "
        "print('multiprocessing' in sys.modules)"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert loaded == "True"
