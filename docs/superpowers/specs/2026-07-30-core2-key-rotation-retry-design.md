# CORE-2 · API key rotation + retry policy — design

**Ticket:** AGE-10 · **Depends on:** CORE-1 (AGE-9) · **Feeds:** CORE-4, CORE-5, BENCH-1

## What this delivers

One LLM call that survives a rate limit, a bad key and a flaky server, without ever spending more
wall clock than it was given.

CORE-1 left two seams: `KeySource`, consulted on every request rather than once at construction,
and `ProviderError`, which carries `status_code`, lowercased response `headers` and `is_timeout`
so a retry layer can tell a rate limit from a server fault without importing a transport. CORE-2
fills both. A `KeyPool` implements `KeySource` over every key `discover_api_keys()` found, and a
`RetryingProvider` decorates `LLMProvider` with the attempt loop.

`OpenAICompatProvider` itself is untouched — that is what the `KeySource` seam bought. The one
edit to `openai_compat.py` is `provider_from_config()`, which is the module's assembly function
and whose whole job is to know what gets wrapped in what.

## Scope boundary

CORE-2 owns *how many times* and *with which key*. It does not own *how long the task has* —
that is CORE-5. The retry loop takes a **relative** budget, `max_elapsed_seconds`, which restarts
at every `complete()` call. When CORE-5 exists it will pass a smaller value derived from the real
remaining wall clock; until then the default stands on its own and nothing has to be rewritten to
accept it.

An absolute deadline was considered and rejected. It would have to be rebuilt per task, so
`provider_from_config()` would stop returning a usable provider and start returning a factory;
it would couple CORE-2 to an orchestrator that does not exist yet; and a retrier holding an
expired deadline fails every call without trying, which is a footgun in tests and in a
long-running process. The relative budget gives the same protection with none of that, and CORE-5
can tighten it later without CORE-2 ever learning what a deadline is.

## The lifetime that makes parking simple

The card asks for "park-until-tomorrow for daily-quota exhaustion". The process it runs in lives
for one task — 120 s for MBPP — then writes its JSON and exits. There is never a tomorrow.

Parking a key for six minutes and parking it until tomorrow therefore have exactly the same
observable effect: the key does not serve again this run. So there are not two mechanisms to
build, a short cooldown and a long park, but **one `available_at` deadline per key**. Time zones,
midnight rollover and sliding-versus-fixed quota windows all disappear with the distinction, and
`time.monotonic` is the right clock precisely because it cannot outlive the process.

This holds only as long as the pool is in-memory. A pool that survived the process — state shared
across tasks on disk — would need real dates and a genuine park/cooldown split. That is a
different design and it is not in this card.

## Module layout

```
src/agent_smith/llm/
├── keypool.py         # KeyPool, AllKeysParked
└── retry.py           # RetryingProvider
```

Two modules, because the two objects have different reasons to change: "when do we try again"
and "which key is healthy" are separate policies, and each is testable without the other — the
pool without a fake provider, the retrier without real keys. Neither imports `httpx`, so the
import-boundary test that CORE-1 introduced keeps passing.

`protocol.py` does not change. `KeySource` stays a one-method protocol and `KeyPool` satisfies it
as written.

## `KeyPool`

```python
KeyPool(keys: Sequence[str], *, clock: Callable[[], float] = time.monotonic)

    def api_key(self) -> str
    def penalise(self, error: ProviderError) -> None
```

State is one `available_at: float` per key, plus a round-robin cursor and the index of the key
most recently lent. No failure counters, no history: a key is either serving now or serving at a
known instant, and nothing else about its past changes what the pool does next.

`api_key()` returns the next key whose `available_at` has passed, advancing the cursor, and
records which one it lent. When every key is parked it raises `AllKeysParked`, carrying the
earliest `available_at` of the pool — the caller needs to know not just that it failed but when
it is worth asking again.

`penalise(error)` applies the error to the key `api_key()` last returned. The retrier never names
a key, and no key ever leaves the pool: keys are secrets, and the narrowest interface that closes
the feedback loop is the one that never puts a secret in a caller's hands.

**The implicit "last lent" is deliberate, and it is only correct because calls are sequential.**
`httpx.Client` is synchronous and the agent loop is single-threaded, so exactly one request is in
flight at a time and "the key we just lent" is unambiguous. A concurrent caller would corrupt the
attribution, penalising whichever key happened to be lent most recently. Nothing in scope is
concurrent; the constraint is documented in the class docstring rather than defended with a lock
that would only give a false impression of safety.

### `_penalty_seconds(error) -> float | None`

A pure function. `None` means the key is not at fault and nothing is parked.

| `ProviderError` | penalty |
|---|---|
| `429` with a readable `retry-after` | that many seconds |
| `429` without one | `DEFAULT_COOLDOWN_SECONDS` (60 s) |
| `401`, `403` | `math.inf` (the key itself is rejected) |
| `5xx` | `None` |
| `is_timeout` | `None` |
| `status_code is None` | `None` |
| any other status | `None` |

`math.inf` rather than a large number: given the section above, "permanent" and "for the rest of
this run" are the same statement, and infinity says it without picking an arbitrary horizon. It
also flows correctly through `AllKeysParked` — an infinite `available_at` yields an infinite
wait, which no budget accommodates, so the retrier stops instead of sleeping.

`retry-after` is read as an integer number of seconds. The HTTP-date form is legal but no
OpenAI-compatible provider we target sends it; an unreadable value falls back to
`DEFAULT_COOLDOWN_SECONDS` rather than raising, because a malformed header is not a reason to
lose a key.

## `RetryingProvider`

```python
RetryingProvider(inner: LLMProvider, pool: KeyPool, *,
                 max_attempts: int = 3,
                 max_elapsed_seconds: float = 20.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[float, float], float] = random.uniform)
```

It takes a concrete `KeyPool`, not a new protocol. There is no second implementation in sight,
and a real pool with an injected clock is a better test double than a spy would be — it exercises
the parking arithmetic instead of asserting that a method was called. YAGNI.

`inner` is typed by one Protocol local to `retry.py`:

```python
class ValidatingProvider(Protocol):
    def complete(self, messages, stop=None, max_tokens=None) -> LLMResponse: ...
    def validate_model(self) -> None: ...
```

`LLMProvider` alone would not do, because `RetryingProvider` has to forward `validate_model()` —
see Construction. It lives in `retry.py` rather than `protocol.py` because it describes what this
decorator needs from what it wraps, not a contract the project codes against.

`clock`, `sleep` and `jitter` are all injected, which is what lets the whole test suite run
without sleeping for real or flaking on a random draw.

### The loop

```
started = clock()
last = None
for attempt in range(max_attempts):
    if attempt and elapsed() >= max_elapsed_seconds:
        break
    try:
        return inner.complete(...).after_retries(attempt)
    except AllKeysParked as parked:
        last, wait = parked, parked.available_at - clock()
    except ProviderError as error:
        last = error
        pool.penalise(error)
        wait = _retry_delay(error, attempt)
        if wait is None:
            raise
    if attempt + 1 == max_attempts:
        break
    if wait and not _sleep_if_it_fits(wait, started):
        break
raise last
```

The `attempt + 1 == max_attempts` guard is what stops the loop sleeping after its final attempt.
Without it the last failure still draws a backoff and waits it out before giving up — wall clock
spent on a wake-up that never comes.

`pool.penalise(error)` runs for **every** `ProviderError`, unconditionally. It is
`_penalty_seconds` that answers `None` on a 5xx or a timeout and parks nothing. The retrier
therefore never asks itself whether an error concerns the keys — that question has exactly one
answer and it lives in the pool. This is the boundary the whole design is arranged around.

`_sleep_if_it_fits(wait, started)` returns `False` without sleeping when `elapsed + wait` would
exceed `max_elapsed_seconds`. The loop never sleeps in order to wake up too late, and that single
rule covers both the backoff and the wait for a parked key.

On giving up, the last `ProviderError` is re-raised as it was: its message already names the real
cause, and wrapping it would bury that behind a summary. A non-retryable error is raised from
inside the loop, so it reaches the caller on the first attempt with no delay at all.

### `_retry_delay(error, attempt, jitter) -> float | None`

The retrier's one classification function, so that no status code is judged in two places. It
takes `jitter` as an argument rather than reading it off an instance, which keeps it a
module-level pure function: the tests pass a deterministic draw and assert the delay exactly.

| result | meaning |
|---|---|
| `None` | give up; the error propagates unchanged |
| `0.0` | try again immediately, on another key |
| `> 0` | sleep that long first |

| `ProviderError` | delay |
|---|---|
| `429` | `0.0` |
| `401`, `403` | `0.0` |
| `5xx` | backoff |
| `is_timeout` | backoff |
| `status_code is None` | backoff |
| any other status | `None` |

It is a whitelist: anything not named gives `None`. A `400`, `404`, `413` or `422` condemns the
request itself, and resending it unchanged produces the identical answer — CORE-4 gets the error
and can change the prompt, which is something this layer cannot do.

`429` and `401/403` return `0.0` rather than a backoff because the pool has just parked the
offending key and the next attempt goes out on a different one. There is nothing to wait for. It
is only when no key at all remains that `api_key()` raises `AllKeysParked` and the loop considers
sleeping.

A completion that arrives empty, or with a body the provider cannot read, is **not** retried. It
falls under "any other status" — those errors carry `200`. The same prompt mostly produces the
same non-answer, and an attempt costs tokens and wall clock that the budget cannot spare.

**Backoff** is `jitter(0, min(0.5 * 2 ** attempt, 4.0))` — full jitter, capped at 4 s. Full
rather than equal jitter because several keys hitting the same provider should spread out. At the
default `max_attempts=3` the ceiling never binds — the draws come from `[0, 0.5]`, `[0, 1]` and
`[0, 2]`. It is there for a caller that raises `max_attempts`, where the doubling would otherwise
reach 8 s at the fifth attempt and blow a 20 s budget on one sleep.

### `AllKeysParked`

```python
class AllKeysParked(ProviderError):
    available_at: float
```

A subclass, so CORE-4 keeps catching exactly one type and its "never raises" obligation stays as
cheap to honour as CORE-1 made it. `openai_compat.py` needs no knowledge of it: the pool raises
it from `api_key()`, which the provider calls inside `complete()`, and it is not an `httpx` error
so it passes through the transport handlers untouched.

## Reporting the attempt count

`LLMResponse` is frozen and `retries` already exists on it, documented by CORE-1 as `0` meaning
"the first attempt succeeded" — the field was put there for this card to fill. Writing it needs
one new method on the model:

```python
def after_retries(self, count: int) -> "LLMResponse":
    """The same completion, recording how many attempts it took to get it."""
    return LLMResponse(**self.model_dump(exclude={"retries"}), retries=count)
```

`exclude={"retries"}` is load-bearing: `model_dump()` returns the field too, and passing it
alongside the keyword argument is a duplicate-argument `TypeError`.

Constructing rather than `model_copy(update=...)`, because `model_copy` skips validation. The
copy is named and belongs to the type that owns the field, instead of leaving a pydantic idiom in
the middle of the retry loop.

`StepMetrics.retries` is then filled by CORE-4 straight from the response, and `total_llm_calls`
is the sum of `1 + retries` over the steps. CORE-2 does not aggregate anything.

## Construction

`provider_from_config()` builds the pool **once** and passes the same object twice — to the
provider as its `key_source`, to the retrier as its pool. Two pools would leave the feedback loop
open: the retrier would penalise a pool nobody draws keys from.

It returns the `RetryingProvider`, which delegates `validate_model()` to the provider it wraps.
Nothing calls `provider_from_config()` or `validate_model()` yet — CORE-4 will be the first — so
the shape is chosen here rather than inherited. The assembled object is what a CLI wants, and a
startup check is part of what it does.

`StaticKeySource` stays where it is. It is what `OpenAICompatProvider` is tested against in
isolation, and deleting it would force every provider test to build a pool it does not care
about.

## Testing

Nothing sleeps and nothing reaches the network. `clock`, `sleep` and `jitter` are injected
everywhere.

**Pool.** Round-robin order over three keys. `penalise` lands on the key `api_key()` last
returned. A `429` carrying `retry-after: 30` parks for exactly 30 s. A `429` with no header parks
for 60 s. A `401` parks past any plausible run. A `5xx` and a timeout park nothing. Every key
parked raises `AllKeysParked` carrying the earliest deadline. A parked key serves again once the
injected clock passes its deadline. A single-key pool rotates onto itself and still parks.

**Retrier.** Success on the first attempt gives `retries=0` and never calls `sleep`. Success on
the third gives `retries=2`. A `400` propagates immediately, before any sleep. The budget stops
the loop before `max_attempts` is reached. `_sleep_if_it_fits` refuses a sleep that would end
past the budget, and the loop stops instead of sleeping. `AllKeysParked` inside the budget sleeps
then retries; outside it, it propagates. The error raised on give-up is the last one observed,
not the first.

**Classification.** `_retry_delay` and `_penalty_seconds` are pure, so both tables above become
parametrised cases directly.

**Integration.** `RetryingProvider` over a real `KeyPool` and a fake `LLMProvider` — no `httpx`.
Three keys, the first two answering `429`, the third succeeding: the result carries `retries=2`
and the two rate-limited keys are parked.

**Boundary.** `KeyPool` satisfies `KeySource`, asserted the way CORE-1 asserts its protocols.
`keypool.py` and `retry.py` import no transport, asserted by the existing import-boundary test.

## Deferred

- **A real wall-clock budget.** CORE-5 passes a `max_elapsed_seconds` derived from the task's
  remaining time. CORE-2 ships a default and the parameter to override it.
- **Persisting parked keys across processes.** Out of scope while the process lives one task; see
  the lifetime section for what would have to change.
- **Reading `x-ratelimit-reset-*`.** `retry-after` covers the providers in scope. The extra
  headers are vendor-shaped and would earn their parsing only if a target provider omitted
  `retry-after`.
- **Concurrency.** The "last lent" attribution assumes one request in flight. Making the pool
  safe under concurrency means leasing keys explicitly, which changes `KeySource`.
