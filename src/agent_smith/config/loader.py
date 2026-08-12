import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from agent_smith.config.errors import ConfigError
from agent_smith.config.schema import ModelsConfig
from agent_smith.models.contract import SandboxConfig

T = TypeVar("T", bound=BaseModel)


def load_models_config(path: Path) -> ModelsConfig:
    """Load the provider/model catalogue.

    `ModelsConfig` is ours, so it declares `extra="forbid"` and rejects typos on
    its own, at every level of nesting.
    """
    return _validate(path, _read_json(path), ModelsConfig)


def load_sandbox_config(path: Path) -> SandboxConfig:
    """Load a sandbox template into the frozen contract's own model.

    Ours is a development default: at evaluation the grader hands us its own
    `SandboxConfig`, so this file never has to match the exam's allowlist.
    """
    data = _read_json(path)
    _reject_unknown_keys(path, data, SandboxConfig)
    return _validate(path, data, SandboxConfig)


def _read_json(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc


def _validate(path: Path, data: Any, model: type[T]) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path} does not match {model.__name__}:\n{exc}") from exc


def _reject_unknown_keys(path: Path, data: Any, model: type[BaseModel]) -> None:
    """Fail on a key the model does not know, instead of dropping it."""
    if not isinstance(data, dict):
        return

    unknown = set(data) - set(model.model_fields)
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s) {sorted(unknown)} for {model.__name__}. "
            f"Known fields: {sorted(model.model_fields)}"
        )
