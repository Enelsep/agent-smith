"""One completion, retried within a budget and across the key pool."""

import random
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from agent_smith.llm.errors import ProviderError
from agent_smith.llm.keypool import AllKeysParked, KeyPool
from agent_smith.llm.protocol import Message
from agent_smith.llm.response import LLMResponse

# Three attempts, because the budget below is what actually stops the loop and
# a count is only the backstop against an endpoint failing instantly forever.
DEFAULT_MAX_ATTEMPTS = 3

# A sixth of one MBPP task's 120 s, which several calls have to share. Shorter
# than the 30 s socket timeout, so an endpoint that hangs gets one attempt and
# no more. CORE-5 passes a smaller number once it knows the real remaining wall
# clock.
DEFAULT_MAX_ELAPSED_SECONDS = 20.0

_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 4.0

# The two families the pool has just parked a key for. Another key is already
# waiting, so there is nothing to sleep for.
_ROTATE_NOW = frozenset({401, 403, 429})

Jitter = Callable[[float, float], float]


class ValidatingProvider(Protocol):
    """What this decorator needs from what it wraps.

    `LLMProvider` plus the startup check, because a wrapper that hid
    `validate_model()` would cost the caller a check CORE-1 built.
    """

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    def validate_model(self) -> None: ...


def _backoff(attempt: int, jitter: Jitter) -> float:
    """Full jitter, so several keys hitting one provider spread out.

    At the default attempt count the draws are `[0, 0.5]`, `[0, 1]` and
    `[0, 2]`, and only the first two are ever slept on: the loop computes the
    final attempt's delay and then breaks, so 1 s is the longest wait actually
    taken.

    The cap first clips at `attempt = 4`, where doubling reaches 8 s and would
    spend a whole budget on one sleep. With the last draw discarded, that puts
    it at `max_attempts = 6` before the cap changes any sleep a caller sees.
    """
    return jitter(0.0, min(_BACKOFF_BASE_SECONDS * 2**attempt, _BACKOFF_CAP_SECONDS))


def _retry_delay(error: ProviderError, attempt: int, jitter: Jitter) -> float | None:
    """How long to wait before trying again. `None` means do not try again.

    A whitelist: anything unnamed gives `None`. A 400, 404, 413 or 422
    condemns the request itself and resending it unchanged returns the
    identical answer — as does a 200 whose body would not parse. Those go back
    to the orchestrator, which can change the prompt, which this layer cannot.

    `0.0` rather than a backoff for a parked key, because the next attempt goes
    out on a different key and waiting would buy nothing.
    """
    if error.status_code in _ROTATE_NOW:
        return 0.0
    if error.is_timeout or error.status_code is None:
        return _backoff(attempt, jitter)
    if 500 <= error.status_code < 600:
        return _backoff(attempt, jitter)
    return None


class RetryingProvider:
    """Asks the provider it wraps again when the failure is worth repeating.

    Implements `LLMProvider`. Decides only *whether* to try again and *how
    long* to wait; whether a key is at fault is the pool's question, and
    `penalise()` is called on every failure so this class never has to ask it.

    `max_elapsed_seconds` restarts at each `complete()` call. It is a bound on
    one completion, not on the task: CORE-5 owns the task's wall clock and will
    pass a smaller value derived from what is left of it.
    """

    def __init__(
        self,
        inner: ValidatingProvider,
        pool: KeyPool,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_elapsed_seconds: float = DEFAULT_MAX_ELAPSED_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Jitter = random.uniform,
    ) -> None:
        self._inner = inner
        self._pool = pool
        self._max_attempts = max(1, max_attempts)
        self._max_elapsed_seconds = max_elapsed_seconds
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter

    def validate_model(self) -> None:
        """Run the startup check of the provider this wraps."""
        self._inner.validate_model()

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """One completion, retried while it is worth it and the budget allows."""
        started = self._clock()
        last: ProviderError | None = None
        for attempt in range(self._max_attempts):
            if attempt and self._elapsed(started) >= self._max_elapsed_seconds:
                break
            try:
                answer = self._inner.complete(messages, stop, max_tokens)
            except AllKeysParked as parked:
                last = parked
                wait = parked.available_at - self._clock()
            except ProviderError as error:
                last = error
                self._pool.penalise(error)
                delay = _retry_delay(error, attempt, self._jitter)
                if delay is None:
                    raise
                wait = delay
            else:
                return answer.after_retries(attempt)
            if attempt + 1 == self._max_attempts:
                break
            if wait > 0 and not self._sleep_if_it_fits(wait, started):
                break
        if last is None:  # pragma: no cover - max_attempts is at least 1
            raise ProviderError("the retry loop made no attempt")
        raise last

    def _elapsed(self, started: float) -> float:
        return self._clock() - started

    def _sleep_if_it_fits(self, wait: float, started: float) -> bool:
        """Sleep, unless waking up would already be past the budget."""
        if self._elapsed(started) + wait >= self._max_elapsed_seconds:
            return False
        self._sleep(wait)
        return True
