"""The one module in the project that speaks HTTP.

Everything outbound happens here, and it is one thing: the inference call. The
endpoint comes from `--provider-url` by way of `ResolvedConfig`, never from a
constant, so a new OpenAI-compatible provider costs a URL and nothing else.
"""

import sys
import time
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from agent_smith.config import ConfigError, ResolvedConfig
from agent_smith.llm.errors import ProviderError
from agent_smith.llm.keypool import KeyPool
from agent_smith.llm.protocol import KeySource, Message
from agent_smith.llm.response import LLMResponse
from agent_smith.llm.retry import RetryingProvider

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
    def timeout(self) -> float | None:
        """The read timeout of the client this provider calls through.

        The read timeout rather than the whole `httpx.Timeout`: it is the one
        that fires on the failure this guards against, an endpoint that accepts
        the connection and then goes quiet. Returning the httpx object would
        also put a transport type on the one public surface whose purpose is to
        keep it contained. `None` means the injected client was built without
        one.
        """
        read_timeout = self._client.timeout.read
        return None if read_timeout is None else float(read_timeout)

    def validate_model(self) -> None:
        """Check the configured model against what the endpoint says it serves.

        Strict about a mismatch, tolerant about an absent catalogue. A wrong
        model name fails every call that follows, so failing in 200 ms is worth
        it. An unreachable `/models` proves nothing about the model, and
        refusing to start would trade a certain failure for a hypothetical one.

        Explicitly called at startup rather than done in `__init__`: building a
        provider must not perform I/O.
        """
        try:
            response = self._client.get(
                self.models_url,
                headers={"Authorization": f"Bearer {self._keys.api_key()}"},
            )
            response.raise_for_status()
            served = [str(entry["id"]) for entry in response.json()["data"]]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            self._warn(f"cannot list the models served by {self.base_url}: {exc}")
            return

        if not served:
            self._warn(f"{self.base_url} lists no model at all")
            return

        if self.model not in served:
            raise ConfigError(
                f"model {self.model!r} is not served by {self.base_url}. "
                f"Available: {', '.join(sorted(served))}"
            )

    def _warn(self, message: str) -> None:
        print(f"warning: {message}; continuing without validation", file=sys.stderr)

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
) -> RetryingProvider:
    """Build the provider the resolved configuration describes.

    The one place that knows the assembly order, so no command has to.

    The pool is built once and passed twice — to the provider as its key
    source, to the retrier as its pool. Two pools would leave the feedback loop
    open: the retrier would park keys in a pool nobody draws from.
    """
    pool = KeyPool(config.api_keys)
    provider = OpenAICompatProvider(
        base_url=config.base_url,
        model=config.model_name,
        key_source=pool,
        stop=list(config.stop),
        max_tokens=config.max_tokens,
        client=client,
    )
    return RetryingProvider(provider, pool)
