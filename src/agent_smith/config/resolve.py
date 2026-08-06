"""Turning the runtime inputs into the one object the rest of the system reads.

`--provider-url`, `--model-name`, the supplied `.env` and the two JSON config
files go in; a validated `ResolvedConfig` comes out. CORE-1 reads its endpoint
and generation settings from it, CORE-2 builds its rotation pool from
`api_keys`.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agent_smith.config.errors import ConfigError
from agent_smith.config.keys import (
    candidate_key_names,
    discover_api_keys,
    prefixed_api_keys,
    provider_prefix_from_url,
)
from agent_smith.config.loader import load_models_config, load_sandbox_config
from agent_smith.config.schema import ModelsConfig
from agent_smith.models.contract import SandboxConfig

# Consulted only when no --provider-url is given: preferred over the rest of
# the catalogue when the environment holds keys for several providers, and the
# one a keyless environment falls back to, so the resulting error names its
# variables instead of whichever provider happens to be last in models.json.
DEV_DEFAULT_PROVIDER = "groq"

DEFAULT_MODELS_PATH = Path("models.json")
DEFAULT_SANDBOX_PATH = Path("sandbox_template.json")


class ResolvedConfig(BaseModel):
    """Everything the run needs, resolved once at startup."""

    provider_name: str
    base_url: str
    model_name: str
    stop: list[str] = Field(default_factory=list)
    max_tokens: int | None = None
    api_keys: list[str] = Field(default_factory=list)
    sandbox: SandboxConfig

    def __repr__(self) -> str:
        """Render the config with the key values replaced by their count.

        This object ends up in debug output and crash reports; the keys must
        not travel with it. `api_keys` stays readable for CORE-2 — the masking
        guards against accidental printing, not against use.
        """
        count = len(self.api_keys)
        return (
            f"ResolvedConfig(provider_name={self.provider_name!r}, "
            f"base_url={self.base_url!r}, model_name={self.model_name!r}, "
            f"stop={self.stop!r}, max_tokens={self.max_tokens!r}, "
            f"api_keys=<{count} key{'' if count == 1 else 's'}>, "
            f"sandbox={self.sandbox!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()


def resolve_config(
    *,
    provider_url: str | None = None,
    model_name: str | None = None,
    models_path: Path = DEFAULT_MODELS_PATH,
    sandbox_path: Path = DEFAULT_SANDBOX_PATH,
    env: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> ResolvedConfig:
    """Resolve the runtime configuration, or raise `ConfigError` explaining why not.

    Pass `env` to work off an explicit mapping; leave it out and the process
    environment is used, after `.env` has been loaded into it. `load_dotenv`
    does not override variables that are already set, so a real environment
    variable always wins over the file.
    """
    if env is None:
        load_dotenv(env_file)
        env = os.environ

    catalogue = load_models_config(models_path)

    if provider_url is None:
        provider_url = _default_url(catalogue, models_path, env)

    base_url = provider_url
    prefix = provider_prefix_from_url(base_url)
    provider_name = prefix.lower()
    provider = catalogue.providers.get(provider_name)

    if model_name is None:
        if provider is None:
            raise ConfigError(
                f"no --model-name given and provider {provider_name!r} is absent from "
                f"{models_path}, so there is no default model to fall back on"
            )
        model_name = provider.default_model

    # An endpoint or a model we have never catalogued is not an error: the
    # point of SETUP-3 is that a new OpenAI-compatible provider works by
    # changing the URL. It just runs on defaults instead of tuned settings.
    settings = provider.models.get(model_name) if provider is not None else None

    api_keys = discover_api_keys(prefix, env)
    if not api_keys:
        raise ConfigError(
            f"no API key found for provider {provider_name!r}. Looked for: "
            + ", ".join(candidate_key_names(prefix))
        )

    return ResolvedConfig(
        provider_name=provider_name,
        base_url=base_url,
        model_name=model_name,
        stop=list(settings.stop) if settings is not None else [],
        max_tokens=settings.max_tokens if settings is not None else None,
        api_keys=api_keys,
        sandbox=load_sandbox_config(sandbox_path),
    )


def _default_url(
    catalogue: ModelsConfig, models_path: Path, env: Mapping[str, str]
) -> str:
    """The endpoint to use when the caller named none, chosen from the keys.

    The `.env` is written by whoever runs us — the subject's own example names
    `OPENROUTER_API_KEY` — so a fixed default provider turns a perfectly good
    key for another one into a fatal error before the first call. Each
    catalogued provider is asked whether the environment carries a key under
    its own prefix, and the first that does wins.

    `DEV_DEFAULT_PROVIDER` is tried before the rest, so an environment holding
    several keys resolves exactly as it did before this existed, and it is also
    what a keyless environment falls back to, so the error names its variables
    rather than the last provider in the file.
    """
    default = catalogue.providers.get(DEV_DEFAULT_PROVIDER)
    if default is None:
        raise ConfigError(
            f"no --provider-url given and the development default "
            f"{DEV_DEFAULT_PROVIDER!r} is absent from {models_path}"
        )

    candidates = [default, *catalogue.providers.values()]
    for provider in candidates:
        prefix = provider_prefix_from_url(provider.base_url)
        if prefixed_api_keys(prefix, env):
            return provider.base_url

    return default.base_url
