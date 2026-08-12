"""Several API keys in rotation, and knowing which are worth using."""

import math
import time
from collections.abc import Callable, Sequence

from agent_smith.config import ConfigError
from agent_smith.llm.errors import ProviderError

DEFAULT_COOLDOWN_SECONDS = 5.0

_RATE_LIMITED = 429
_KEY_REJECTED = frozenset({401, 402, 403})


class AllKeysParked(ProviderError):
    """Every key is rate limited or rejected, and none can serve right now.

    A `ProviderError` subclass, so the orchestrator keeps catching one type.
    `available_at` is when the first of them frees up: the caller needs to know
    not only that the pool refused but whether waiting is worth it.
    """

    def __init__(self, message: str, *, available_at: float) -> None:
        super().__init__(message)
        self.available_at = available_at


def _announced_wait(error: ProviderError) -> float:
    """How long the provider asked us to wait, in seconds.

    `retry-after` may legally be an HTTP date; no endpoint we target sends one,
    and a value we cannot read is not a reason to lose a key, so anything that
    is not a positive number of seconds falls back to the default. Zero and
    negatives fall back too — honouring them literally would put the key
    straight back into rotation and spin on the same rate limit.
    """
    raw = error.headers.get("retry-after")
    if raw is None:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        return DEFAULT_COOLDOWN_SECONDS
    return seconds if seconds > 0 else DEFAULT_COOLDOWN_SECONDS


def _penalty_seconds(error: ProviderError) -> float | None:
    """How long this error costs the key, or `None` if the key is not at fault.

    Only two families are the key's doing. A rate limit is the quota attached
    to that key; a 401 or 403 is the key itself being refused. Server faults,
    timeouts and malformed requests would fail identically on any key, so
    parking one would throw away capacity to fix nothing.

    `math.inf` for a refused key, because the process lives one task: there is
    no later in which it could recover.
    """
    if error.status_code in _KEY_REJECTED:
        return math.inf
    if error.status_code == _RATE_LIMITED:
        return _announced_wait(error)
    return None


class KeyPool:
    """Hands out the API keys `discover_api_keys()` found, one per request.

    Implements `KeySource`, so the provider consults it on every request and a
    different key can serve from one call to the next.

    `penalise()` applies to the key `api_key()` last returned rather than to a
    key the caller names. That keeps the feedback loop from ever putting a
    secret in a caller's hands, and it is correct because exactly one request
    is in flight at a time: `httpx.Client` is synchronous and the agent loop is
    single-threaded. A concurrent caller would mis-attribute failures, and
    nothing here defends against that.

    `clock` is injected because the pool measures durations and nothing else:
    handing it a clock the caller controls is what lets every test of parking
    run without sleeping.
    """

    def __init__(
        self,
        keys: Sequence[str],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not keys:
            raise ConfigError("cannot build a key pool without an API key")
        self._keys = list(keys)
        self._clock = clock
        self._cursor = 0
        self._available_at = [0.0] * len(self._keys)
        self._lent: int | None = None

    def api_key(self) -> str:
        """The next key that is free to serve.

        Raises `AllKeysParked` when none is, carrying the earliest deadline.
        """
        now = self._clock()
        for offset in range(len(self._keys)):
            index = (self._cursor + offset) % len(self._keys)
            if self._available_at[index] <= now:
                self._cursor = (index + 1) % len(self._keys)
                self._lent = index
                return self._keys[index]
        raise AllKeysParked(
            f"all {len(self._keys)} API keys are rate limited or rejected",
            available_at=min(self._available_at),
        )

    def penalise(self, error: ProviderError) -> None:
        """Charge `error` to the key that was last lent, if it is at fault."""
        if self._lent is None:
            return
        penalty = _penalty_seconds(error)
        if penalty is None:
            return
        self._available_at[self._lent] = self._clock() + penalty
