"""The provider against an OpenAI-compatible endpoint.

Every test here drives a real `httpx.Client` over a `MockTransport`, so the
request we build and the response we parse are exercised rather than faked.
Nothing reaches the network.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from agent_smith.config import ConfigError, ResolvedConfig
from agent_smith.llm import LLMProvider, LLMResponse, Message, ProviderError
from agent_smith.llm.openai_compat import (
    DEFAULT_TIMEOUT_SECONDS,
    OpenAICompatProvider,
    StaticKeySource,
    provider_from_config,
)
from agent_smith.llm.retry import RetryingProvider
from agent_smith.models.contract import SandboxConfig

Handler = Callable[[httpx.Request], httpx.Response]


def _sandbox() -> SandboxConfig:
    return SandboxConfig(
        authorized_imports=["math"],
        allowed_directories=["/tmp"],
        max_execution_time_seconds=5,
        max_memory_mb=256,
    )


def _config(**overrides: object) -> ResolvedConfig:
    base: dict[str, object] = {
        "provider_name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model_name": "llama-3.3-70b-versatile",
        "stop": ["Observation:"],
        "max_tokens": 1500,
        "api_keys": ["key-one", "key-two"],
        "sandbox": _sandbox(),
    }
    base.update(overrides)
    return ResolvedConfig.model_validate(base)


def _exploding_client() -> httpx.Client:
    """A client that fails the test if anything is sent through it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request expected, got {request.method} {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _provider(config: ResolvedConfig, *, client: httpx.Client) -> OpenAICompatProvider:
    """Build the provider directly, the way these isolation tests need it.

    These tests exercise `OpenAICompatProvider`'s own contract — one request,
    no retries — so they construct it themselves rather than through
    `provider_from_config`, which also assembles the retry policy around it.
    """
    return OpenAICompatProvider(
        base_url=config.base_url,
        model=config.model_name,
        key_source=StaticKeySource(config.api_keys),
        stop=list(config.stop),
        max_tokens=config.max_tokens,
        client=client,
    )


class TestStaticKeySource:
    def test_it_serves_the_first_key_of_the_pool(self) -> None:
        assert StaticKeySource(["first", "second"]).api_key() == "first"

    def test_it_serves_the_same_key_every_time(self) -> None:
        source = StaticKeySource(["first", "second"])
        assert [source.api_key(), source.api_key()] == ["first", "first"]

    def test_no_key_at_all_is_a_configuration_fault(self) -> None:
        with pytest.raises(ConfigError):
            StaticKeySource([])


def test_the_provider_satisfies_the_protocol_the_rest_of_the_system_imports() -> None:
    """Without an annotation somewhere, a Protocol checks nothing.

    mypy verifies structural conformance where a concrete type is assigned to
    the Protocol, and nowhere else. This annotation is that place: it is what
    stops `complete()` drifting out of the contract CORE-2 and CORE-4 code
    against. The assertion is incidental; the type annotation is the test.
    """
    provider: LLMProvider = _provider(_config(), client=_exploding_client())
    assert provider.complete is not None


class TestConstruction:
    def test_building_a_provider_sends_nothing(self) -> None:
        # validate_model() is an explicit startup step precisely so that
        # construction stays free of I/O: tests and offline work need it.
        OpenAICompatProvider(
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile",
            key_source=StaticKeySource(["key-one"]),
            client=_exploding_client(),
        )

    def test_it_reads_its_endpoint_and_defaults_off_the_resolved_config(self) -> None:
        provider = _provider(_config(), client=_exploding_client())
        assert provider.completions_url == (
            "https://api.groq.com/openai/v1/chat/completions"
        )
        assert provider.models_url == "https://api.groq.com/openai/v1/models"
        assert provider.model == "llama-3.3-70b-versatile"

    def test_a_trailing_slash_on_the_base_url_does_not_double_up(self) -> None:
        provider = _provider(
            _config(base_url="https://api.groq.com/openai/v1/"),
            client=_exploding_client(),
        )
        assert provider.completions_url == (
            "https://api.groq.com/openai/v1/chat/completions"
        )

    def test_the_client_it_builds_carries_the_documented_timeout(self) -> None:
        # A guard against a hung socket, not a budget: MBPP allows 120 s of
        # wall clock for a whole task.
        provider = OpenAICompatProvider(
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile",
            key_source=StaticKeySource(["key-one"]),
        )
        assert provider.timeout == DEFAULT_TIMEOUT_SECONDS

    def test_an_explicit_timeout_reaches_the_client(self) -> None:
        provider = OpenAICompatProvider(
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile",
            key_source=StaticKeySource(["key-one"]),
            timeout=12.5,
        )
        assert provider.timeout == 12.5


def _completion_body(
    content: str = "print(1)",
    prompt_tokens: int = 12,
    completion_tokens: int = 3,
    model: str = "llama-3.3-70b-versatile",
) -> dict[str, Any]:
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def _recording_provider(
    sent: list[httpx.Request],
    body: dict[str, Any] | None = None,
    status_code: int = 200,
    **overrides: object,
) -> OpenAICompatProvider:
    """A provider whose transport records what it was asked to send."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status_code, json=body or _completion_body())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return _provider(_config(**overrides), client=client)


_PROMPT: list[Message] = [{"role": "user", "content": "add two numbers"}]


class TestCompleteRequest:
    def test_it_posts_to_the_chat_completions_route_of_the_given_base_url(self) -> None:
        sent: list[httpx.Request] = []
        _recording_provider(sent).complete(_PROMPT)
        assert sent[0].method == "POST"
        assert str(sent[0].url) == ("https://api.groq.com/openai/v1/chat/completions")

    def test_it_authenticates_with_the_key_the_source_supplies(self) -> None:
        sent: list[httpx.Request] = []
        _recording_provider(sent).complete(_PROMPT)
        assert sent[0].headers["authorization"] == "Bearer key-one"

    def test_it_sends_the_model_and_the_messages(self) -> None:
        sent: list[httpx.Request] = []
        _recording_provider(sent).complete(_PROMPT)
        payload = json.loads(sent[0].content)
        assert payload["model"] == "llama-3.3-70b-versatile"
        assert payload["messages"] == [{"role": "user", "content": "add two numbers"}]

    def test_the_configured_defaults_apply_when_the_caller_says_nothing(self) -> None:
        sent: list[httpx.Request] = []
        _recording_provider(sent).complete(_PROMPT)
        payload = json.loads(sent[0].content)
        assert payload["stop"] == ["Observation:"]
        assert payload["max_tokens"] == 1500

    def test_explicit_arguments_override_the_configured_defaults(self) -> None:
        sent: list[httpx.Request] = []
        _recording_provider(sent).complete(_PROMPT, stop=["END"], max_tokens=64)
        payload = json.loads(sent[0].content)
        assert payload["stop"] == ["END"]
        assert payload["max_tokens"] == 64

    def test_absent_settings_are_omitted_rather_than_sent_as_null(self) -> None:
        # Not every OpenAI-compatible server treats a null the way it treats an
        # absent key.
        sent: list[httpx.Request] = []
        provider = _recording_provider(sent, stop=[], max_tokens=None)
        provider.complete(_PROMPT)
        payload = json.loads(sent[0].content)
        assert "stop" not in payload
        assert "max_tokens" not in payload

    def test_the_key_is_read_again_for_every_request(self) -> None:
        # The seam CORE-2 attaches to: a pool must be free to hand out a
        # different key from one call to the next.
        class RotatingKeys:
            def __init__(self) -> None:
                self._calls = 0

            def api_key(self) -> str:
                self._calls += 1
                return f"key-{self._calls}"

        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json=_completion_body())

        provider = OpenAICompatProvider(
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile",
            key_source=RotatingKeys(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        provider.complete(_PROMPT)
        provider.complete(_PROMPT)
        assert sent[0].headers["authorization"] == "Bearer key-1"
        assert sent[1].headers["authorization"] == "Bearer key-2"


class TestCompleteResponse:
    def test_it_returns_the_generated_text_and_the_token_counts(self) -> None:
        sent: list[httpx.Request] = []
        response = _recording_provider(sent).complete(_PROMPT)
        assert isinstance(response, LLMResponse)
        assert response.text == "print(1)"
        assert response.input_tokens == 12
        assert response.output_tokens == 3

    def test_it_measures_the_call_and_names_the_endpoint(self) -> None:
        sent: list[httpx.Request] = []
        response = _recording_provider(sent).complete(_PROMPT)
        assert response.latency_ms > 0
        assert response.api_url == ("https://api.groq.com/openai/v1/chat/completions")

    def test_it_reports_the_model_the_server_says_it_served(self) -> None:
        sent: list[httpx.Request] = []
        body = _completion_body(model="llama-3.3-70b-versatile-0125")
        response = _recording_provider(sent, body=body).complete(_PROMPT)
        assert response.model == "llama-3.3-70b-versatile-0125"

    def test_it_falls_back_to_the_configured_model_when_none_is_echoed(self) -> None:
        sent: list[httpx.Request] = []
        body = _completion_body()
        del body["model"]
        response = _recording_provider(sent, body=body).complete(_PROMPT)
        assert response.model == "llama-3.3-70b-versatile"

    def test_a_reply_stopped_at_the_token_cap_is_marked_as_cut_short(self) -> None:
        sent: list[httpx.Request] = []
        body = _completion_body(content="# I will first consider the")
        body["choices"][0]["finish_reason"] = "length"

        assert _recording_provider(sent, body=body).complete(_PROMPT).cut_short is True

    def test_a_reply_that_ended_on_its_own_is_not(self) -> None:
        # Including one from an endpoint that reports no finish_reason at all.
        sent: list[httpx.Request] = []
        body = _completion_body()
        body["choices"][0]["finish_reason"] = "stop"

        assert _recording_provider(sent, body=body).complete(_PROMPT).cut_short is False
        assert _recording_provider(sent).complete(_PROMPT).cut_short is False

    def test_a_first_attempt_that_succeeds_reports_no_retries(self) -> None:
        sent: list[httpx.Request] = []
        assert _recording_provider(sent).complete(_PROMPT).retries == 0


def _failing_provider(handler: Handler) -> OpenAICompatProvider:
    """A provider whose transport runs the given handler."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return _provider(_config(), client=client)


def _responding_provider(
    status_code: int,
    body: dict[str, Any] | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> OpenAICompatProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status_code, text=text, headers=headers)
        return httpx.Response(
            status_code, json=body or _completion_body(), headers=headers
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return _provider(_config(), client=client)


class TestCompleteErrors:
    def test_a_timeout_is_flagged_so_core2_can_back_off(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        with pytest.raises(ProviderError) as caught:
            _failing_provider(handler).complete(_PROMPT)
        assert caught.value.is_timeout is True
        assert caught.value.status_code is None

    def test_an_unreachable_host_carries_no_status_and_is_not_a_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        with pytest.raises(ProviderError) as caught:
            _failing_provider(handler).complete(_PROMPT)
        assert caught.value.status_code is None
        assert caught.value.is_timeout is False

    def test_a_rate_limit_carries_the_status_and_the_reset_headers(self) -> None:
        # Exactly what CORE-2 needs to park the key until the reset.
        provider = _responding_provider(
            429,
            text='{"error": "rate limit reached"}',
            headers={"retry-after": "42", "x-ratelimit-reset-tokens": "7.66s"},
        )
        with pytest.raises(ProviderError) as caught:
            provider.complete(_PROMPT)
        assert caught.value.status_code == 429
        assert caught.value.headers["retry-after"] == "42"
        assert caught.value.headers["x-ratelimit-reset-tokens"] == "7.66s"
        assert "rate limit reached" in caught.value.body_excerpt

    def test_a_server_fault_carries_its_status(self) -> None:
        with pytest.raises(ProviderError) as caught:
            _responding_provider(503, text="upstream unavailable").complete(_PROMPT)
        assert caught.value.status_code == 503
        assert caught.value.is_timeout is False

    def test_a_rejected_key_carries_its_status(self) -> None:
        with pytest.raises(ProviderError) as caught:
            _responding_provider(401, text="invalid api key").complete(_PROMPT)
        assert caught.value.status_code == 401

    def test_a_body_that_is_not_json_is_reported_rather_than_crashing(self) -> None:
        with pytest.raises(ProviderError) as caught:
            _responding_provider(200, text="<html>gateway</html>").complete(_PROMPT)
        assert caught.value.status_code == 200

    def test_an_error_smuggled_in_a_200_carries_the_upstream_status(self) -> None:
        # OpenRouter answers 200 and puts the routed provider's failure in the
        # body; the smuggled code must reach CORE-2 so a 502 gets retried
        # instead of ending the run.
        body = {"error": {"message": "Upstream overloaded (33/32)", "code": 502}}
        with pytest.raises(ProviderError) as caught:
            _responding_provider(200, body=body).complete(_PROMPT)
        assert caught.value.status_code == 502
        assert "Upstream overloaded" in str(caught.value)

    def test_a_partial_error_envelope_still_says_something_usable(self) -> None:
        # Both fields are optional on the wire; an envelope missing them must
        # not render as "error None: None".
        with pytest.raises(ProviderError) as caught:
            _responding_provider(200, body={"error": {}}).complete(_PROMPT)
        assert caught.value.status_code is None
        assert "None" not in str(caught.value)
        assert "no status" in str(caught.value)

    def test_missing_token_counts_are_an_error_not_a_zero(self) -> None:
        # A run whose token accounting is absent is unusable, not degraded:
        # a silent 0 would corrupt the totals instead of reporting the problem.
        body = _completion_body()
        del body["usage"]
        with pytest.raises(ProviderError) as caught:
            _responding_provider(200, body=body).complete(_PROMPT)
        assert "usage" in str(caught.value)

    def test_a_response_with_no_choices_is_an_error(self) -> None:
        body = _completion_body()
        body["choices"] = []
        with pytest.raises(ProviderError):
            _responding_provider(200, body=body).complete(_PROMPT)

    def test_an_empty_completion_is_an_error(self) -> None:
        with pytest.raises(ProviderError):
            _responding_provider(200, body=_completion_body(content="")).complete(
                _PROMPT
            )

    def test_no_api_key_ever_reaches_the_exception(self) -> None:
        provider = _responding_provider(401, text="invalid api key")
        with pytest.raises(ProviderError) as caught:
            provider.complete(_PROMPT)
        rendered = f"{caught.value} {caught.value.body_excerpt} {caught.value.headers}"
        assert "key-one" not in rendered


def _models_provider(handler: Handler) -> OpenAICompatProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return _provider(_config(), client=client)


def _serving(*model_ids: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.groq.com/openai/v1/models"
        return httpx.Response(200, json={"data": [{"id": i} for i in model_ids]})

    return handler


class TestValidateModel:
    def test_a_model_the_endpoint_serves_passes_quietly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        provider = _models_provider(_serving("llama-3.3-70b-versatile", "other"))
        provider.validate_model()
        assert capsys.readouterr().err == ""

    def test_a_model_the_endpoint_does_not_serve_stops_the_run(self) -> None:
        # A wrong name fails every call that follows: 200 ms now beats the
        # whole wall-clock budget later.
        provider = _models_provider(_serving("mixtral-8x7b", "gemma-7b"))
        with pytest.raises(ConfigError) as caught:
            provider.validate_model()
        assert "llama-3.3-70b-versatile" in str(caught.value)
        assert "mixtral-8x7b" in str(caught.value)

    def test_an_endpoint_without_the_route_only_warns(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Local inference servers do not all implement /models, and a missing
        # route proves nothing about the model.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        _models_provider(handler).validate_model()
        assert "warning" in capsys.readouterr().err

    def test_an_unreachable_endpoint_only_warns(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow", request=request)

        _models_provider(handler).validate_model()
        assert "warning" in capsys.readouterr().err

    def test_an_unexpected_payload_only_warns(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": ["llama-3.3-70b-versatile"]})

        _models_provider(handler).validate_model()
        assert "warning" in capsys.readouterr().err

    def test_an_empty_catalogue_only_warns(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # An endpoint that answers but serves nothing has told us nothing.
        _models_provider(_serving()).validate_model()
        assert "warning" in capsys.readouterr().err


class TestAssembly:
    def test_the_factory_returns_the_retrying_provider(self) -> None:
        assembled = provider_from_config(_config(), client=_exploding_client())

        assert isinstance(assembled, RetryingProvider)

    def test_the_assembled_provider_still_validates_its_model(self) -> None:
        config = _config()
        client = httpx.Client(
            transport=httpx.MockTransport(_serving(config.model_name))
        )

        provider_from_config(config, client=client).validate_model()

    def test_an_assembled_provider_retries_a_rate_limit_and_answers(self) -> None:
        # One key rate-limited, the next one serves. This is the whole card in
        # one call: the pool parks the first key, the retrier moves on, and the
        # caller gets a completion that says it took two attempts.
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["authorization"])
            if len(seen) == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=_completion_body())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assembled = provider_from_config(
            _config(api_keys=["first", "second"]), client=client
        )
        messages: list[Message] = [{"role": "user", "content": "hi"}]

        result = assembled.complete(messages)

        assert result.retries == 1
        assert result.text == "print(1)"
        assert seen == ["Bearer first", "Bearer second"]

    def test_the_retrier_and_the_provider_draw_from_one_pool(self) -> None:
        # Two pools would leave the feedback loop open: the retrier would park
        # keys in a pool nobody draws from. Asserted on the object rather than
        # through a run: the attempt count is high enough now that a retrier
        # holding its own pool would sit out a cooldown and still answer, so
        # the two assemblies are no longer told apart by what comes back.
        assembled = provider_from_config(_config(api_keys=["first", "second"]))
        inner = assembled._inner
        assert isinstance(inner, OpenAICompatProvider)

        assert assembled._pool is inner._keys
