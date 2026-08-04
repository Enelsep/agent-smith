"""`sandbox_template.json` is what actually configures the sandbox."""

from __future__ import annotations

from pathlib import Path

from agent_smith.config.loader import load_sandbox_config
from agent_smith.models.contract import SandboxConfig
from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "sandbox_template.json"


def test_from_config_maps_all_four_limits() -> None:
    config = SandboxConfig(
        authorized_imports=["math"],
        allowed_directories=["/tmp/agent"],
        max_execution_time_seconds=7,
        max_memory_mb=256,
    )
    sb = Sandbox.from_config(config)

    assert sb.timeout == 7.0
    assert sb.authorized_imports == ["math"]
    assert sb.allowed_directories == ["/tmp/agent"]
    assert sb.max_memory_mb == 256


def test_shipped_template_drives_the_allowlist() -> None:
    config = load_sandbox_config(TEMPLATE)
    with Sandbox.from_config(config) as sb:
        # `queue` is in the template but not in Sandbox.DEFAULT_IMPORTS, so it
        # only works if the file is really the source of truth.
        assert "queue" in config.authorized_imports
        assert sb.execute("import queue\nprint(queue.Queue().empty())").outcome is (
            Outcome.OK
        )
        # Something off the template's list is still refused.
        assert sb.execute("import socket").outcome is Outcome.BLOCKED


def test_empty_config_lists_fall_back_to_class_defaults() -> None:
    # A bare SandboxConfig() has empty lists; a sandbox with no imports at all
    # would be useless, so the class defaults stand in.
    sb = Sandbox.from_config(SandboxConfig())
    assert sb.authorized_imports == Sandbox.DEFAULT_IMPORTS
    assert sb.allowed_directories == Sandbox.DEFAULT_DIRECTORIES
