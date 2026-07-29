"""The one module in the project that speaks HTTP.

Everything outbound happens here, and it is one thing: the inference call. The
endpoint comes from `--provider-url` by way of `ResolvedConfig`, never from a
constant, so a new OpenAI-compatible provider costs a URL and nothing else.
"""

from collections.abc import Sequence

import httpx

from agent_smith.config import ConfigError, ResolvedConfig
from agent_smith.llm.protocol import KeySource

# A guard against a hung socket, not a budget. MBPP allows 120 s of wall clock
# for a task that may take several iterations, so a request that has been
# silent for 30 s has already cost too much. Budgets are CORE-5 and SWE-6.
DEFAULT_TIMEOUT_SECONDS = 30.0


class StaticKeySource:
    """The single key CORE-1 uses.

    SETUP-3 may discover several; this returns the first and ignores the rest.
    That is a dated decision rather than an oversight: rotation across the pool
    is CORE-2, which will implement `KeySource` with its own pool and be
    injected here in place of this class.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        if not keys:
            raise ConfigError("cannot build a provider without an API key")
        self._key = keys[0]

    def api_key(self) -> str:
        return self._key


class OpenAICompatProvider:
    """Asks an OpenAI-compatible endpoint for a completion.

    One request per call, no retries: the retry policy and the key rotation
    are CORE-2, and they attach through `key_source` without reopening this
    file.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        key_source: KeySource,
        *,
        stop: list[str] | None = None,
        max_tokens: int | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        normalised = base_url.rstrip("/")
        self.completions_url = f"{normalised}/chat/completions"
        self.models_url = f"{normalised}/models"
        self.base_url = normalised
        self.model = model
        self._keys = key_source
        self._stop = list(stop or [])
        self._max_tokens = max_tokens
        self._client = client or httpx.Client(timeout=timeout)

    @property
    def timeout(self) -> httpx.Timeout:
        """The timeout of the client this provider calls through."""
        return self._client.timeout


def provider_from_config(
    config: ResolvedConfig, *, client: httpx.Client | None = None
) -> OpenAICompatProvider:
    """Build the provider the resolved configuration describes.

    The one place that knows the assembly order, so no command has to.
    """
    return OpenAICompatProvider(
        base_url=config.base_url,
        model=config.model_name,
        key_source=StaticKeySource(config.api_keys),
        stop=list(config.stop),
        max_tokens=config.max_tokens,
        client=client,
    )
