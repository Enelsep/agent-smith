"""The one module in the project that speaks HTTP.

Everything outbound happens here, and it is one thing: the inference call. The
endpoint comes from `--provider-url` by way of `ResolvedConfig`, never from a
constant, so a new OpenAI-compatible provider costs a URL and nothing else.
"""

import time
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from agent_smith.config import ConfigError, ResolvedConfig
from agent_smith.llm.errors import ProviderError
from agent_smith.llm.protocol import KeySource, Message
from agent_smith.llm.response import LLMResponse

# A guard against a hung socket, not a budget. MBPP allows 120 s of wall clock
# for a task that may take several iterations, so a request that has been
# silent for 30 s has already cost too much. Budgets are CORE-5 and SWE-6.
DEFAULT_TIMEOUT_SECONDS = 30.0


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message


class _Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class _ChatCompletion(BaseModel):
    """One vendor's wire format, not our contract — hence private.

    `usage` is required on purpose. A completion whose token counts are absent
    is not a degraded result but an unusable one: the contract requires
    `total_input_tokens` to equal the sum of the per-step counts, so a silent
    zero would corrupt the record rather than report the problem.
    """

    choices: list[_Choice]
    usage: _Usage
    model: str | None = None


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

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Ask for one completion. One request, no retries."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
        }
        effective_stop = self._stop if stop is None else stop
        if effective_stop:
            payload["stop"] = effective_stop
        effective_max_tokens = self._max_tokens if max_tokens is None else max_tokens
        if effective_max_tokens is not None:
            payload["max_tokens"] = effective_max_tokens

        started = time.perf_counter()
        try:
            response = self._client.post(
                self.completions_url,
                json=payload,
                headers={"Authorization": f"Bearer {self._keys.api_key()}"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"{self.completions_url} did not answer in time",
                is_timeout=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"cannot reach {self.completions_url}: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        if response.status_code != httpx.codes.OK:
            raise ProviderError(
                f"{self.completions_url} answered {response.status_code}",
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.text,
            )

        return self._to_response(response, latency_ms)

    def _to_response(self, response: httpx.Response, latency_ms: float) -> LLMResponse:
        try:
            parsed = _ChatCompletion.model_validate_json(response.content)
        except ValidationError as exc:
            raise ProviderError(
                f"{self.completions_url} returned a body we cannot read: {exc}",
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.text,
            ) from exc

        if not parsed.choices:
            raise ProviderError(
                f"{self.completions_url} returned no choices",
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.text,
            )

        text = parsed.choices[0].message.content
        if not text:
            raise ProviderError(
                f"{self.completions_url} returned an empty completion",
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.text,
            )

        return LLMResponse(
            text=text,
            input_tokens=parsed.usage.prompt_tokens,
            output_tokens=parsed.usage.completion_tokens,
            latency_ms=latency_ms,
            model=parsed.model or self.model,
            api_url=self.completions_url,
        )


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
