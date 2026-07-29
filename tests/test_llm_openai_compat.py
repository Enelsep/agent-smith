"""The provider against an OpenAI-compatible endpoint.

Every test here drives a real `httpx.Client` over a `MockTransport`, so the
request we build and the response we parse are exercised rather than faked.
Nothing reaches the network.
"""

import httpx
import pytest

from agent_smith.config import ConfigError, ResolvedConfig
from agent_smith.llm.openai_compat import (
    DEFAULT_TIMEOUT_SECONDS,
    OpenAICompatProvider,
    StaticKeySource,
    provider_from_config,
)
from agent_smith.models.contract import SandboxConfig


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


class TestStaticKeySource:
    def test_it_serves_the_first_key_of_the_pool(self) -> None:
        assert StaticKeySource(["first", "second"]).api_key() == "first"

    def test_it_serves_the_same_key_every_time(self) -> None:
        source = StaticKeySource(["first", "second"])
        assert [source.api_key(), source.api_key()] == ["first", "first"]

    def test_no_key_at_all_is_a_configuration_fault(self) -> None:
        with pytest.raises(ConfigError):
            StaticKeySource([])


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
        provider = provider_from_config(_config(), client=_exploding_client())
        assert provider.completions_url == (
            "https://api.groq.com/openai/v1/chat/completions"
        )
        assert provider.models_url == "https://api.groq.com/openai/v1/models"
        assert provider.model == "llama-3.3-70b-versatile"

    def test_a_trailing_slash_on_the_base_url_does_not_double_up(self) -> None:
        provider = provider_from_config(
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
        assert provider.timeout.read == DEFAULT_TIMEOUT_SECONDS

    def test_an_explicit_timeout_reaches_the_client(self) -> None:
        provider = OpenAICompatProvider(
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile",
            key_source=StaticKeySource(["key-one"]),
            timeout=12.5,
        )
        assert provider.timeout.read == 12.5
