"""One completion, retried within a budget and across the key pool."""

from collections.abc import Sequence

import pytest

from agent_smith.llm import LLMResponse, Message, ProviderError
from agent_smith.llm.keypool import _KEY_REJECTED, _RATE_LIMITED, AllKeysParked, KeyPool
from agent_smith.llm.retry import (
    _ROTATE_NOW,
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

    `cost` advances the clock before the script is consulted, standing in for
    the wall clock a real HTTP call spends whether it succeeds or fails.
    Defaults to `0.0`, so every test that does not pass it is unaffected.
    """

    def __init__(
        self,
        script: Sequence[object],
        pool: KeyPool,
        clock: FakeClock,
        *,
        cost: float = 0.0,
    ) -> None:
        self._script = list(script)
        self._pool = pool
        self._clock = clock
        self._cost = cost
        self.used_keys: list[str] = []
        self.validated = False

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.used_keys.append(self._pool.api_key())
        self._clock.advance(self._cost)
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
    max_elapsed_seconds: float = DEFAULT_MAX_ELAPSED_SECONDS,
    call_cost: float = 0.0,
) -> tuple[RetryingProvider, FakeProvider, FakeClock, FakeSleep]:
    clock = FakeClock()
    sleep = FakeSleep(clock)
    pool = KeyPool(list(keys), clock=clock)
    inner = FakeProvider(script, pool, clock, cost=call_cost)
    retrier = RetryingProvider(
        inner,
        pool,
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


def test_running_out_of_budget_raises_the_last_error_seen() -> None:
    retrier, _, _, _ = build(
        [
            ProviderError("first", status_code=500),
            ProviderError("second", status_code=500),
            ProviderError("third", status_code=500),
        ],
        max_elapsed_seconds=2.0,
        call_cost=1.0,
    )

    with pytest.raises(ProviderError, match="second"):
        retrier.complete(MESSAGES)


def test_the_last_attempt_does_not_sleep() -> None:
    # Sleeping after the final attempt spends wall clock the loop will never
    # use: there is nothing left to wake up for.
    retrier, _, _, sleep = build(
        [
            ProviderError("boom", status_code=500),
            ProviderError("boom", status_code=500),
            ProviderError("boom", status_code=500),
        ],
        max_elapsed_seconds=2.0,
        call_cost=1.0,
    )

    with pytest.raises(ProviderError):
        retrier.complete(MESSAGES)

    assert sleep.calls == [0.5]


def test_it_forwards_the_startup_check_to_the_provider_it_wraps() -> None:
    retrier, inner, _, _ = build([a_response()])

    retrier.validate_model()

    assert inner.validated is True


def test_the_budget_stops_the_loop_before_the_attempts_run_out() -> None:
    # Three attempts are allowed, but the first sleep consumes the budget.
    retrier, inner, _, sleep = build(
        [
            ProviderError("boom", status_code=500),
            ProviderError("boom", status_code=500),
            a_response(),
        ],
        max_elapsed_seconds=0.6,
    )

    with pytest.raises(ProviderError, match="boom"):
        retrier.complete(MESSAGES)

    assert sleep.calls == [0.5]
    assert len(inner.used_keys) == 2


def test_it_never_sleeps_past_the_budget() -> None:
    retrier, inner, _, sleep = build(
        [ProviderError("boom", status_code=500), a_response()],
        max_elapsed_seconds=0.25,
    )

    with pytest.raises(ProviderError, match="boom"):
        retrier.complete(MESSAGES)

    assert sleep.calls == []
    assert len(inner.used_keys) == 1


def test_it_waits_for_a_parked_pool_when_the_wait_fits() -> None:
    retrier, inner, _, sleep = build(
        [
            ProviderError("slow down", status_code=429, headers={"retry-after": "2"}),
            a_response(),
        ],
        keys=("only",),
    )

    result = retrier.complete(MESSAGES)

    # Three attempts: the 429, the one that found the pool empty and waited,
    # and the one that succeeded. Only two reached the provider, because
    # `api_key()` raised before the second could send anything.
    assert result.retries == 2
    assert sleep.calls == [2.0]
    assert inner.used_keys == ["only", "only"]


def test_it_gives_up_when_the_parked_pool_frees_after_the_budget() -> None:
    retrier, inner, _, sleep = build(
        [
            ProviderError("slow down", status_code=429, headers={"retry-after": "300"}),
            a_response(),
        ],
        keys=("only",),
    )

    # The error that surfaces is the pool refusing, not the 429 that caused it:
    # the last failure seen is what the loop re-raises.
    with pytest.raises(AllKeysParked, match="rate limited or rejected"):
        retrier.complete(MESSAGES)

    assert sleep.calls == []
    assert len(inner.used_keys) == 1


def test_a_rejected_only_key_gives_up_rather_than_waiting_forever() -> None:
    retrier, _, _, sleep = build(
        [ProviderError("unauthorized", status_code=401), a_response()],
        keys=("only",),
    )

    with pytest.raises(AllKeysParked, match="rate limited or rejected"):
        retrier.complete(MESSAGES)

    assert sleep.calls == []


def test_two_rate_limited_keys_are_parked_and_the_third_answers() -> None:
    retrier, inner, _, sleep = build(
        [
            ProviderError("slow down", status_code=429, headers={"retry-after": "60"}),
            ProviderError("slow down", status_code=429, headers={"retry-after": "60"}),
            a_response(),
        ]
    )

    result = retrier.complete(MESSAGES)

    assert result.retries == 2
    assert inner.used_keys == ["a", "b", "c"]
    assert sleep.calls == []


def test_it_refuses_a_sleep_that_would_land_exactly_on_the_budget() -> None:
    # Waking up precisely at the deadline buys no attempt: the top of the loop
    # breaks on `>=`. Sleeping for it would spend wall clock for nothing.
    retrier, inner, _, sleep = build(
        [ProviderError("boom", status_code=500), a_response()],
        max_elapsed_seconds=0.5,
    )

    with pytest.raises(ProviderError, match="boom"):
        retrier.complete(MESSAGES)

    assert sleep.calls == []
    assert len(inner.used_keys) == 1


def test_a_slow_endpoint_exhausts_the_budget_without_ever_sleeping() -> None:
    # A 429 never sleeps: `_retry_delay` gives it `0.0` because the next
    # attempt goes out on a different key. That means `_sleep_if_it_fits` is
    # never even called here, so it cannot be what stops a third attempt from
    # going out over budget — only the check at the top of the loop can. Wall
    # clock is spent by the call itself (`call_cost`), not by backing off.
    retrier, inner, _, sleep = build(
        [
            ProviderError("slow down", status_code=429),
            ProviderError("slow down", status_code=429),
            a_response(),
        ],
        max_elapsed_seconds=20.0,
        call_cost=12.0,
    )

    with pytest.raises(ProviderError, match="slow down"):
        retrier.complete(MESSAGES)

    assert sleep.calls == []
    assert len(inner.used_keys) == 2


def test_a_zero_delay_error_always_costs_the_key_that_earned_it() -> None:
    # What makes a loop bounded only by elapsed time safe. `_retry_delay`
    # returns 0.0 for these, so the next attempt goes out immediately -- and
    # nothing would end the loop if the same key came back round. It cannot:
    # every status that rotates without waiting also parks the key it was lent,
    # so a pool of N keys reaches `AllKeysParked` in N attempts and then has a
    # real wait to sit out. The two sets live in different modules; this is the
    # line that stops them drifting apart.
    assert _ROTATE_NOW == _KEY_REJECTED | {_RATE_LIMITED}


def test_the_pool_running_dry_ends_the_loop_rather_than_spinning() -> None:
    # The same invariant, exercised: three keys, every one rate limited, no
    # wall clock spent on the calls themselves. The loop cannot turn forever.
    retrier, inner, _, _ = build(
        [ProviderError("slow down", status_code=429)] * 3,
        keys=("a", "b", "c"),
        max_elapsed_seconds=5.0,
    )

    with pytest.raises(AllKeysParked):
        retrier.complete(MESSAGES)

    assert inner.used_keys == ["a", "b", "c"]
