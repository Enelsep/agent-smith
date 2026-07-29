"""The provider contract: what CORE-2 and CORE-4 import.

Nothing here performs I/O, which is the point: the retry layer and the
orchestrator depend on these three modules without pulling in a transport.
"""

import pytest
from pydantic import ValidationError

from agent_smith.llm import LLMResponse, ProviderError
from agent_smith.llm.errors import BODY_EXCERPT_LIMIT


class TestProviderError:
    def test_it_defaults_to_a_cause_the_retry_layer_cannot_act_on(self) -> None:
        error = ProviderError("something went wrong")
        assert error.status_code is None
        assert error.headers == {}
        assert error.body_excerpt == ""
        assert error.is_timeout is False

    def test_it_carries_what_core2_needs_to_park_a_key(self) -> None:
        error = ProviderError(
            "rate limited",
            status_code=429,
            headers={"retry-after": "30"},
            body='{"error": "slow down"}',
        )
        assert error.status_code == 429
        assert error.headers["retry-after"] == "30"
        assert error.body_excerpt == '{"error": "slow down"}'

    def test_a_timeout_is_flagged_rather_than_inferred_from_a_message(self) -> None:
        assert ProviderError("timed out", is_timeout=True).is_timeout is True

    def test_a_long_body_is_truncated_so_it_stays_printable(self) -> None:
        error = ProviderError("bad body", body="x" * (BODY_EXCERPT_LIMIT + 100))
        assert len(error.body_excerpt) == BODY_EXCERPT_LIMIT

    def test_the_message_survives_as_the_str_of_the_exception(self) -> None:
        assert str(ProviderError("cannot reach the endpoint")) == (
            "cannot reach the endpoint"
        )


class TestLLMResponse:
    def test_it_records_the_half_of_stepmetrics_only_the_provider_knows(self) -> None:
        response = LLMResponse(
            text="print(1)",
            input_tokens=12,
            output_tokens=3,
            latency_ms=41.5,
            model="llama-3.3-70b-versatile",
            api_url="https://api.groq.com/openai/v1/chat/completions",
        )
        assert response.text == "print(1)"
        assert response.input_tokens == 12
        assert response.output_tokens == 3
        assert response.latency_ms == 41.5
        assert response.model == "llama-3.3-70b-versatile"
        assert response.api_url.endswith("/chat/completions")

    def test_retries_defaults_to_zero_meaning_the_first_attempt_worked(self) -> None:
        response = LLMResponse(
            text="ok",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
            model="m",
            api_url="https://example.com/v1/chat/completions",
        )
        assert response.retries == 0

    def test_it_is_frozen_because_a_measurement_is_not_state(self) -> None:
        response = LLMResponse(
            text="ok",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
            model="m",
            api_url="https://example.com/v1/chat/completions",
        )
        with pytest.raises(ValidationError):
            # setattr rather than `response.text = ...`: the Pydantic mypy
            # plugin is not enabled, so a direct assignment would need a
            # `type: ignore` that mypy then reports as unused.
            setattr(response, "text", "tampered")  # noqa: B010
