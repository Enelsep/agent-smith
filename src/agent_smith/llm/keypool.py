"""Several API keys in rotation, and knowing which are worth using."""

import time
from collections.abc import Callable, Sequence

from agent_smith.config import ConfigError


class KeyPool:
    """Hands out the API keys `discover_api_keys()` found, one per request.

    Implements `KeySource`, so the provider consults it on every request and a
    different key can serve from one call to the next.

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

    def api_key(self) -> str:
        """The next key in the rotation."""
        key = self._keys[self._cursor]
        self._cursor = (self._cursor + 1) % len(self._keys)
        return key
