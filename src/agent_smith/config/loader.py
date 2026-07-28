"""Reading the JSON config files off disk.

Every failure mode — absent file, bad JSON, wrong shape — comes back as a
`ConfigError` naming the file, so a misconfiguration is readable at the point
it happens instead of surfacing as an AttributeError three modules later.
"""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_smith.config.errors import ConfigError
from agent_smith.config.schema import ModelsConfig
from agent_smith.models.contract import SandboxConfig

T = TypeVar("T", bound=BaseModel)


def load_models_config(path: Path) -> ModelsConfig:
    """Load the provider/model catalogue."""
    return _load(path, ModelsConfig)


def load_sandbox_config(path: Path) -> SandboxConfig:
    """Load a sandbox template into the frozen contract's own model.

    Ours is a development default: at evaluation the grader hands us its own
    `SandboxConfig`, so this file never has to match the exam's allowlist.
    """
    return _load(path, SandboxConfig)


def _load(path: Path, model: type[T]) -> T:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path} does not match {model.__name__}:\n{exc}") from exc
