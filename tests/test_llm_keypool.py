"""Several API keys in rotation, and knowing which are worth using."""

import math

import pytest

from agent_smith.config import ConfigError
from agent_smith.llm import KeySource
from agent_smith.llm.errors import ProviderError
from agent_smith.llm.keypool import DEFAULT_COOLDOWN_SECONDS, AllKeysParked, KeyPool


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


def rate_limited(retry_after: str | None = None) -> ProviderError:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return ProviderError("slow down", status_code=429, headers=headers)


# What parks the key that was just lent, and for how long. `None` means the
# error says nothing about the key and nothing is parked.
PENALTIES: list[tuple[str, ProviderError, float | None]] = [
    ("429 with a wait", rate_limited("30"), 30.0),
    ("429 with no wait", rate_limited(), DEFAULT_COOLDOWN_SECONDS),
    ("429 with junk", rate_limited("soon"), DEFAULT_COOLDOWN_SECONDS),
    (
        "429 with an http date",
        rate_limited("Tue, 15 Nov 1994 08:12:31 GMT"),
        DEFAULT_COOLDOWN_SECONDS,
    ),
    ("429 with zero", rate_limited("0"), DEFAULT_COOLDOWN_SECONDS),
    ("429 with a negative wait", rate_limited("-5"), DEFAULT_COOLDOWN_SECONDS),
    ("401", ProviderError("unauthorized", status_code=401), math.inf),
    # A monthly allowance does not reopen inside a run, so the key is parked
    # for good rather than cooled down. Measured 2026-08-09: six runs ended on
    # a 402 while the second key of the pool was answering 200.
    ("402", ProviderError("payment required", status_code=402), math.inf),
    ("403", ProviderError("forbidden", status_code=403), math.inf),
    ("500", ProviderError("boom", status_code=500), None),
    ("503", ProviderError("overloaded", status_code=503), None),
    ("timeout", ProviderError("timed out", is_timeout=True), None),
    ("no response at all", ProviderError("unreachable"), None),
    ("400", ProviderError("bad request", status_code=400), None),
    ("404", ProviderError("no such model", status_code=404), None),
    ("422", ProviderError("unprocessable", status_code=422), None),
    ("200 with an unreadable body", ProviderError("garbage", status_code=200), None),
]


@pytest.mark.parametrize(
    ("error", "parked_for"),
    [(error, parked_for) for _, error, parked_for in PENALTIES],
    ids=[name for name, _, _ in PENALTIES],
)
def test_what_parks_the_lent_key_and_for_how_long(
    error: ProviderError, parked_for: float | None
) -> None:
    clock = FakeClock()
    pool = KeyPool(["only"], clock=clock)
    pool.api_key()

    pool.penalise(error)

    if parked_for is None:
        assert pool.api_key() == "only"
        return
    with pytest.raises(AllKeysParked) as parked:
        pool.api_key()
    assert parked.value.available_at == parked_for


def test_a_parked_key_serves_again_once_its_deadline_passes() -> None:
    clock = FakeClock()
    pool = KeyPool(["only"], clock=clock)
    pool.api_key()
    pool.penalise(rate_limited("30"))

    clock.advance(29.0)
    with pytest.raises(AllKeysParked):
        pool.api_key()

    clock.advance(1.0)
    assert pool.api_key() == "only"


def test_the_penalty_lands_on_the_key_that_was_lent_not_the_next_one() -> None:
    clock = FakeClock()
    pool = KeyPool(["a", "b"], clock=clock)
    assert pool.api_key() == "a"

    pool.penalise(rate_limited("30"))

    assert pool.api_key() == "b"
    assert pool.api_key() == "b"


def test_all_keys_parked_reports_the_earliest_deadline() -> None:
    clock = FakeClock()
    pool = KeyPool(["a", "b"], clock=clock)
    pool.api_key()
    pool.penalise(rate_limited("90"))
    pool.api_key()
    pool.penalise(rate_limited("30"))

    with pytest.raises(AllKeysParked) as parked:
        pool.api_key()

    assert parked.value.available_at == 30.0


def test_all_keys_parked_never_names_a_key() -> None:
    pool = KeyPool(["not-a-real-key"], clock=FakeClock())
    pool.api_key()
    pool.penalise(ProviderError("unauthorized", status_code=401))

    with pytest.raises(AllKeysParked) as parked:
        pool.api_key()

    assert "not-a-real-key" not in str(parked.value)


def test_penalising_before_any_key_was_lent_does_nothing() -> None:
    pool = KeyPool(["only"], clock=FakeClock())

    pool.penalise(rate_limited("30"))

    assert pool.api_key() == "only"


def test_a_pool_is_a_key_source() -> None:
    # The seam CORE-1 left. Asserted rather than assumed, because the provider
    # is typed against the protocol and nothing else would catch a drift.
    pool: KeySource = KeyPool(["only"], clock=FakeClock())

    assert pool.api_key() == "only"
