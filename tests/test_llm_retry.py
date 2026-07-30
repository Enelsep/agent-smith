"""One completion, retried within a budget and across the key pool."""

from collections.abc import Sequence

import pytest

from agent_smith.llm import LLMResponse, Message, ProviderError
from agent_smith.llm.keypool import KeyPool
from agent_smith.llm.retry import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_ELAPSED_SECONDS,
    RetryingProvider,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleep:
    """Records what it was asked to sleep, and moves the clock by that much."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(seconds)


def no_jitter(low: float, high: float) -> float:
    """The top of the range, so a backoff test asserts an exact number."""
    return high


def a_response(text: str = "ok") -> LLMResponse:
    return LLMResponse(
        text=text,
        input_tokens=11,
        output_tokens=7,
        latency_ms=120.5,
        model="qwen",
        api_url="https://example.invalid/v1/chat/completions",
    )


class FakeProvider:
    """Answers from a script, drawing a key first exactly as the real one does.

    Drawing the key matters: it is what makes the pool raise `AllKeysParked`
    from inside `complete()`, which is where the retrier has to meet it.
    """

    def __init__(self, script: Sequence[object], pool: KeyPool) -> None:
        self._script = list(script)
        self._pool = pool
        self.used_keys: list[str] = []
        self.validated = False

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.used_keys.append(self._pool.api_key())
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, LLMResponse)
        return outcome

    def validate_model(self) -> None:
        self.validated = True


MESSAGES: list[Message] = [{"role": "user", "content": "hi"}]


def build(
    script: Sequence[object],
    keys: Sequence[str] = ("a", "b", "c"),
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_elapsed_seconds: float = DEFAULT_MAX_ELAPSED_SECONDS,
) -> tuple[RetryingProvider, FakeProvider, FakeClock, FakeSleep]:
    clock = FakeClock()
    sleep = FakeSleep(clock)
    pool = KeyPool(list(keys), clock=clock)
    inner = FakeProvider(script, pool)
    retrier = RetryingProvider(
        inner,
        pool,
        max_attempts=max_attempts,
        max_elapsed_seconds=max_elapsed_seconds,
        clock=clock,
        sleep=sleep,
        jitter=no_jitter,
    )
    return retrier, inner, clock, sleep


def test_a_first_attempt_that_works_reports_no_retries() -> None:
    retrier, inner, _, sleep = build([a_response()])

    result = retrier.complete(MESSAGES)

    assert result.retries == 0
    assert result.text == "ok"
    assert sleep.calls == []
    assert len(inner.used_keys) == 1


def test_a_rate_limit_moves_to_the_next_key_without_sleeping() -> None:
    retrier, inner, _, sleep = build(
        [ProviderError("slow down", status_code=429), a_response()]
    )

    result = retrier.complete(MESSAGES)

    assert result.retries == 1
    assert inner.used_keys == ["a", "b"]
    assert sleep.calls == []


def test_a_rejected_key_moves_to_the_next_key_without_sleeping() -> None:
    retrier, inner, _, sleep = build(
        [ProviderError("unauthorized", status_code=401), a_response()]
    )

    result = retrier.complete(MESSAGES)

    assert result.retries == 1
    assert inner.used_keys == ["a", "b"]
    assert sleep.calls == []


def test_a_server_fault_is_retried_after_a_backoff() -> None:
    retrier, inner, _, sleep = build(
        [ProviderError("boom", status_code=503), a_response()]
    )

    result = retrier.complete(MESSAGES)

    assert result.retries == 1
    assert sleep.calls == [0.5]
    assert inner.used_keys == ["a", "b"]


def test_the_backoff_doubles_between_attempts() -> None:
    retrier, _, _, sleep = build(
        [
            ProviderError("boom", status_code=500),
            ProviderError("boom", status_code=500),
            a_response(),
        ]
    )

    assert retrier.complete(MESSAGES).retries == 2
    assert sleep.calls == [0.5, 1.0]


@pytest.mark.parametrize(
    "error",
    [
        ProviderError("timed out", is_timeout=True),
        ProviderError("unreachable"),
    ],
    ids=["timeout", "no response at all"],
)
def test_a_transport_failure_is_retried_with_a_backoff(error: ProviderError) -> None:
    retrier, _, _, sleep = build([error, a_response()])

    assert retrier.complete(MESSAGES).retries == 1
    assert sleep.calls == [0.5]


@pytest.mark.parametrize("status", [400, 404, 413, 422, 200])
def test_an_error_another_attempt_cannot_fix_is_raised_at_once(status: int) -> None:
    retrier, inner, _, sleep = build([ProviderError("no", status_code=status)])

    with pytest.raises(ProviderError) as raised:
        retrier.complete(MESSAGES)

    assert raised.value.status_code == status
    assert len(inner.used_keys) == 1
    assert sleep.calls == []


def test_running_out_of_attempts_raises_the_last_error_seen() -> None:
    retrier, _, _, _ = build(
        [
            ProviderError("first", status_code=500),
            ProviderError("second", status_code=500),
            ProviderError("third", status_code=500),
        ]
    )

    with pytest.raises(ProviderError, match="third"):
        retrier.complete(MESSAGES)


def test_the_last_attempt_does_not_sleep() -> None:
    # Sleeping after the final attempt spends wall clock the loop will never
    # use: there is nothing left to wake up for.
    retrier, _, _, sleep = build(
        [
            ProviderError("boom", status_code=500),
            ProviderError("boom", status_code=500),
            ProviderError("boom", status_code=500),
        ]
    )

    with pytest.raises(ProviderError):
        retrier.complete(MESSAGES)

    assert sleep.calls == [0.5, 1.0]


def test_it_forwards_the_startup_check_to_the_provider_it_wraps() -> None:
    retrier, inner, _, _ = build([a_response()])

    retrier.validate_model()

    assert inner.validated is True
