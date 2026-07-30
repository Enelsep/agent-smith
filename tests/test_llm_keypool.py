"""Several API keys in rotation, and knowing which are worth using."""

import pytest

from agent_smith.config import ConfigError
from agent_smith.llm.keypool import KeyPool


class FakeClock:
    """A clock the test moves by hand, so nothing ever really sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_pool_needs_at_least_one_key() -> None:
    with pytest.raises(ConfigError):
        KeyPool([])


def test_keys_are_handed_out_in_turn() -> None:
    pool = KeyPool(["a", "b", "c"], clock=FakeClock())

    assert [pool.api_key() for _ in range(4)] == ["a", "b", "c", "a"]


def test_a_single_key_pool_keeps_returning_it() -> None:
    pool = KeyPool(["only"], clock=FakeClock())

    assert [pool.api_key() for _ in range(3)] == ["only", "only", "only"]
