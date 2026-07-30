# CORE-1 · Provider abstraction — design

**Ticket:** AGE-9 · **Depends on:** SETUP-3 (AGE-7) · **Feeds:** CORE-2, CORE-4, CORE-5, BENCH-1

## What this delivers

One way to ask a model for a completion, over an endpoint chosen at runtime.

`ResolvedConfig` already carries everything the call needs — `base_url`, `model_name`, `stop`,
`max_tokens`, `api_keys`. CORE-1 turns that into an `LLMProvider` with a single method,
`complete()`, and one startup check, `validate_model()`. The endpoint is always read from
`--provider-url`, never from a constant: switching provider is a URL change, which is the whole
point of the configuration work that precedes this.

## Scope boundary

CORE-1 makes **one HTTP request per `complete()` call**. No retry loop, no backoff, no key
rotation — those are CORE-2, and CORE-1's job is to leave a seam they can attach to rather than
code they have to unpick.

The seam is a one-method protocol:

```python
class KeySource(Protocol):
    def api_key(self) -> str: ...
```

CORE-1 ships `StaticKeySource`, which returns `api_keys[0]` and nothing else. CORE-2 makes its
`KeyPool` implement `KeySource` and injects it in place; no line of `openai_compat.py` changes.

The provider calls `key_source.api_key()` **on every request**, never once in the constructor.
That is the detail that makes rotation possible later: a pool must be free to return a different
key from one call to the next.

`LLMResponse.retries` is therefore always `0` in CORE-1. This is not a placeholder — it is the
correct value. The contract defines `retries` as the number of LLM API retries, where `0` means
the first attempt succeeded.

## Module layout

```
src/agent_smith/llm/
├── __init__.py        # re-exports the contract: LLMProvider, KeySource, Message,
│                      #                          LLMResponse, ProviderError
├── errors.py          # ProviderError
├── response.py        # LLMResponse (frozen)
├── protocol.py        # LLMProvider, KeySource, Message
└── openai_compat.py   # OpenAICompatProvider, StaticKeySource, provider_from_config
```

The split mirrors `config/`, and it earns its keep: `errors.py`, `response.py` and `protocol.py`
do not import the HTTP client, and neither does `__init__.py` — it re-exports the contract and
stops there. So `import agent_smith.llm` loads no transport, and CORE-2 and CORE-4 depend on the
contract without dragging one in. Reaching the provider means naming `openai_compat` at the
import site, which is precisely where an HTTP surface should be visible. "Nothing above the
provider talks HTTP" becomes a test rather than an intention.

A layer separating HTTP transport from the OpenAI wire format was considered and rejected. It
would pay off only if a second wire format were coming; everything in scope is OpenAI-compatible
by design, so each side of that boundary would have exactly one occupant. YAGNI.

## The contract

`LLMResponse` is frozen. It is a measurement, not mutable state: CORE-4 reads it to build a
`StepMetrics` and has no reason to alter it. Its fields are the LLM half of `StepMetrics`, which
CORE-4 completes with `step`, `sandbox_input` and `sandbox_output`:

| `LLMResponse` | `StepMetrics` |
| --- | --- |
| `text` | `llm_output` |
| `input_tokens` | `input_tokens` |
| `output_tokens` | `output_tokens` |
| `latency_ms` | `request_time_ms` |
| `model` | `model_name` |
| `api_url` | `api_url` |
| `retries` | `retries` |

`api_url` holds the **full** endpoint that was called (`{base_url}/chat/completions`), not the
base URL. It is per-step evidence of what was contacted, so it should name what was contacted.

Messages are a `TypedDict`, not a bare `dict[str, str]`:

```python
class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str
```

Under `disallow_untyped_defs` and `warn_return_any`, this gives mypy something to check at the
call site. A generic dict says nothing.

`role` is a `Literal` for the same reason. No endpoint rejects a misspelt role: it is forwarded,
and the model answers something plausible to a conversation we did not mean to send. The three
values are what a ReAct loop needs, observations coming back as user turns. Native tool-calling
would want a `tool` role, but it would equally want `tool_calls` and `tool_call_id` in this
TypedDict — so the day the Literal is too narrow, the shape is too narrow with it.

## Construction

```python
OpenAICompatProvider(
    base_url: str,
    model: str,
    key_source: KeySource,
    stop: list[str] | None = None,
    max_tokens: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
)
```

`provider_from_config(cfg: ResolvedConfig) -> OpenAICompatProvider` is the one place that knows how
to go from the resolved configuration to a provider: it wires `base_url`, `model_name`, the
per-model `stop` and `max_tokens` defaults, and a `StaticKeySource` over `cfg.api_keys`. The CLI
calls that and nothing else, so no command has to remember the assembly order.

`client` exists so tests can inject a `MockTransport`; it defaults to a client the provider owns.

`DEFAULT_TIMEOUT_SECONDS = 30.0`. This is a guard against a hung socket, not a budget: MBPP allows
120 seconds of wall clock for a task that may take several iterations, so a single request that has
gone quiet for 30 seconds has already cost too much. Real budget enforcement is CORE-5 and SWE-6.

## `complete(messages, stop=None, max_tokens=None)`

```
POST {base_url}/chat/completions
  headers  Authorization: Bearer <key_source.api_key()>
  json     model, messages, stop?, max_tokens?
  timing   perf_counter() around the POST → latency_ms
```

`stop` and `max_tokens` default to `None`, meaning "use the provider's configured defaults" — the
per-model settings `ResolvedConfig` read out of `models.json`. An explicit argument overrides them.
So CORE-4 normally calls `complete(messages)`, and CORE-5 can lower `max_tokens` for a single call
without rebuilding the provider.

When a value resolves to `None`, the key is **omitted from the JSON body** rather than sent as
`null`. OpenAI-compatible servers do not all treat the two the same way.

The response body is validated by a private Pydantic model rather than by subscripting nested
dicts. We already depend on Pydantic, and it turns an opaque `KeyError` into an error that names
the missing field. That model stays private to the module: it is one vendor's wire format, not
part of our contract.

```
choices[0].message.content   → text
usage.prompt_tokens          → input_tokens
usage.completion_tokens      → output_tokens
```

**A missing `usage` block is an error, not a zero.** The contract requires `total_input_tokens` to
equal the sum of the per-step `input_tokens`, and the record is checked for that consistency. A run
whose token counts are absent is not a degraded run, it is an unusable one — and a silent `0` would
corrupt the totals rather than report the problem. Same for an absent or empty `content`.

## `validate_model()`

`GET {base_url}/models`, read `data[].id`, compare against the configured model name:

| Outcome | Behaviour |
| --- | --- |
| Model present in the list | Silent |
| List returned, model absent | `ConfigError` naming the available models |
| 404, timeout, connection error, unexpected JSON | Warning on stderr, run continues |

The asymmetry is deliberate. A wrong model name fails **every** call that follows, so finding out
in 200 ms beats finding out after the wall-clock budget is spent. An unreachable `/models` proves
nothing about the model — refusing to start would trade a certain failure for a hypothetical one.
Local inference servers, which the `LOCAL` prefix convention already anticipates, do not always
implement the route.

This is an explicit method called by the CLI at startup, never something the constructor does.
Building a provider must not perform I/O: tests and offline work need to construct one freely.

## Error surface

`httpx` goes in, `ProviderError` comes out. Nothing else crosses the boundary.

```python
class ProviderError(Exception):
    status_code: int | None
    headers: Mapping[str, str]
    body_excerpt: str
    is_timeout: bool
```

| Cause | `status_code` | `is_timeout` | What CORE-2 will do |
| --- | --- | --- | --- |
| Timeout | `None` | `True` | Backoff |
| Connection / DNS failure | `None` | `False` | Backoff |
| 429 | `429` | `False` | Park the key, read `retry-after` |
| 5xx | The code | `False` | Backoff |
| 4xx other than 429 | The code | `False` | Configuration fault, no retry |
| 200 with invalid body, missing `usage`, empty content | `200` | `False` | No retry, it will recur |

One type keeps CORE-4's "must never raise" obligation to a single `except`; the populated fields
let CORE-2 make its decisions without ever importing the HTTP client.

`headers` are the **response** headers. That is where `retry-after` and `x-ratelimit-reset-*`
live, and it means the `Authorization` header cannot end up inside an exception. `body_excerpt` is
truncated to 500 characters. No API key appears anywhere in `ProviderError` — the same discipline
as `ResolvedConfig.__repr__`, for the same reason: these objects end up in debug output.

## Outbound HTTP and the review surface

A blanket grep for `httpx` / `urllib` / `requests` across submitted Python sources is a standard
anti-cheat signal — an HTTP client *could* be used to fetch solutions. Ours cannot be avoided: the
inference call is HTTP. So the goal is not to dodge the match but to keep the surface small enough
to be read as legitimate at a glance.

Four files match the pattern, and no more:

- `src/agent_smith/llm/openai_compat.py` — the only production module that makes HTTP requests;
- `tests/test_llm_openai_compat.py` — every wire test, deliberately in one file rather than eight;
- `tests/test_llm_import_boundary.py` — the test that enforces the first point;
- `src/agent_smith/config/keys.py` — `from urllib.parse import urlparse`, which parses the
  provider URL into a host. It matches the pattern because the pattern is a substring search, but
  it opens no connection.

That last one sets the shape of the boundary test: it cannot ban the `urllib` package outright,
or it would fail on correct, already-merged code. It bans HTTP *clients* — `httpx` outside the
provider module, plus `requests`, `http.client` and `urllib.request` anywhere — while allowing
`urllib.parse`. That is the same distinction a reviewer makes by eye, so it is the one worth
encoding.

Plus a README line stating that our only outbound HTTP is the inference call, and that the
`urllib.parse` import parses URLs rather than fetching them.

We do **not** build the module name dynamically to slip past the grep. It would work, and it would
be the worst decision available: a legitimate use disguised as evasion, found by a reviewer who was
looking for exactly that. Few files, plainly named, is the honest answer and also the better one.

## Testing

Everything offline, through `httpx.MockTransport`. No test touches the network, and the real
client is exercised — request construction and response parsing included — rather than bypassed.

- **Request** — full URL, `Authorization` header, `model`; `stop`/`max_tokens` omitted when `None`
  and present when given.
- **Response** — `text`, both token counts, `latency_ms > 0`, full `api_url`, `model`,
  `retries == 0`.
- **Errors** — every row of the table above, including a 429 whose `retry-after` is verified to
  reach the caller.
- **The CORE-2 seam** — a `KeySource` returning a different key each call must produce two
  different `Authorization` headers across two `complete()` calls. This is what guarantees
  rotation can be added without reopening `openai_compat.py`.
- **`validate_model`** — all five outcomes.
- **Import boundary** — no module outside `openai_compat.py` imports an HTTP client, with
  `urllib.parse` explicitly allowed and `urllib.request` explicitly not.

## Deferred

Retry loops, backoff, key rotation and rate-limit cooldown are CORE-2. Keys beyond the first are
loaded by SETUP-3 and deliberately unused until then; `StaticKeySource` says so in its docstring,
so that "we load N keys and use one" reads as a dated decision rather than an oversight.
