from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    """Per-model generation settings. Both are optional; sane defaults apply."""

    model_config = ConfigDict(extra="forbid")

    stop: list[str] = Field(default_factory=list)
    max_tokens: int | None = Field(default=None, gt=0)


class ProviderConfig(BaseModel):
    """One OpenAI-compatible endpoint and the models we know about on it."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    default_model: str
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    benchmark_defaults: dict[str, str] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    """The whole catalogue, keyed by lowercase provider name."""

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig]
