"""Internal models for `models.json`.

Kept deliberately apart from `models/contract.py`: that file mirrors the
contract the evaluation imposes on us and must not grow fields of our own.
This one describes a catalogue we are free to change.

`extra="forbid"` throughout, so a mistyped key is an error rather than a
setting that silently stays on its default — `max_token` instead of
`max_tokens` would otherwise cost us a truncated completion with no signal.
"""

from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    """Per-model generation settings. Both are optional; sane defaults apply."""

    model_config = ConfigDict(extra="forbid")

    stop: list[str] = Field(default_factory=list)
    max_tokens: int | None = None


class ProviderConfig(BaseModel):
    """One OpenAI-compatible endpoint and the models we know about on it."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    default_model: str
    models: dict[str, ModelConfig] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    """The whole catalogue, keyed by lowercase provider name."""

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig]
