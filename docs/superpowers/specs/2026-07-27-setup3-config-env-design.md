# SETUP-3 — Config files & env loading (design)

*Stream B · owner Dev 2 (jerbarth) · Linear AGE-7 · depends on SETUP-2 (frozen contract).*

## Purpose

Give the agent a single, provider-agnostic way to turn the runtime inputs
(`--provider-url`, `--model-name`, the supplied `.env`, and the JSON config files) into one
validated object the rest of the system consumes. We do not control the `.env` handed to us at
evaluation time nor the exact key names in it, so key discovery must be robust to several
conventions and must derive the provider from the URL rather than a hardcoded constant.

## Scope

**In.** Loading and validating `models.json` and `sandbox_template.json`; loading `.env` via
`python-dotenv`; deriving the provider from `--provider-url`; discovering the ordered list of API
keys for that provider; producing a `ResolvedConfig`. Committing `.env.example`.

**Out (deferred to CORE-2).** Key rotation, cooldown, round-robin, retry/backoff. SETUP-3 only
*discovers* the keys and hands them over as an ordered list; the `KeyPool` that cycles them lives in
CORE-2.

## Module layout

A small `src/agent_smith/config/` package, split so the logic-heavy parts are pure and trivially
testable:

- **`keys.py`** — pure functions, no I/O:
  - `provider_prefix_from_url(url: str) -> str`
  - `discover_api_keys(prefix: str, env: Mapping[str, str]) -> list[str]`
- **`loader.py`** — file I/O + validation: `load_models_config(path)`, `load_sandbox_config(path)`
  (the latter returns the frozen contract's `SandboxConfig`). `load_sandbox_config` **rejects
  unknown keys explicitly**, because `SandboxConfig` is not ours to give `extra="forbid"` and every
  one of its fields has a default: writing `authorized_import` would otherwise validate cleanly and
  hand back an empty allowlist — every import refused at run time, with nothing to explain why.
  `models.json` needs no such guard, its models are ours and already forbid extras.
- **`schema.py`** — internal Pydantic models for `models.json` (`ModelsConfig`, `ProviderConfig`,
  `ModelConfig`). These are *internal* models, kept separate from the frozen `contract.py`.
- **`errors.py`** — the `ConfigError` raised by the whole package. Its own module so `keys.py` can
  raise it without importing the orchestration layer that imports `keys.py`.
- **`resolve.py`** — orchestration: `resolve_config(...) -> ResolvedConfig`, plus the
  `ResolvedConfig` model.
- **`__init__.py`** — re-exports `resolve_config`, `ResolvedConfig`, `ConfigError`.

## Data flow

1. The CLI passes `--provider-url` and `--model-name` (both optional; Groq is the dev default when
   absent).
2. `load_dotenv()` loads `.env` into the process environment.
3. **Provider prefix** — `provider_prefix_from_url` uppercases the host's second-level domain, which
   already yields `GROQ`, `OPENROUTER`, `MISTRAL`, `TOGETHER` and so on with nothing hardcoded.
   A small override table carries only the hosts that rule gets *wrong* — today just
   `googleapis.com → GOOGLE`, since the second-level domain would read `GOOGLEAPIS`. Listing
   providers the fallback already handles would turn the table into a support list and suggest we
   endorse some endpoints over others, which is the opposite of what this module is for.
   An **address is not a provider**: `127.0.0.1`, `[::1]`, a private IP or `localhost` all resolve
   to `LOCAL`, because the second-level rule would read `0` off `127.0.0.1` — not even a legal
   variable name. Local inference servers (Ollama, vLLM, llama.cpp) are a realistic development
   endpoint, and `LOCAL_API_KEY` gives them a usable convention.
4. **Key discovery** — `discover_api_keys(prefix, env)`:
   - collect, in order: `<PREFIX>_API_KEY`, then `<PREFIX>_API_KEY_2` … `_32`, then
     `<PREFIX>_API_KEYS` split on commas;
   - the numbered scan **tolerates holes** and is bounded instead of stopping at the first gap.
     Commenting a burnt key out of a `.env` must disable that key, not every key after it —
     silently shrinking the pool is exactly what starves CORE-2 of keys to rotate through when the
     rate limits hit, and it would happen under exam pressure;
   - merge preserving first-seen order and drop duplicates and blanks;
   - if the result is empty, fall back to generic names `LLM_API_KEY`, then `API_KEY` — most
     specific first, because a bare `API_KEY` in a `.env` we did not write could belong to any
     service;
   - the generic names are a *fallback*, never an addition: a stray `API_KEY` sitting next to a
     prefixed one almost certainly belongs to something else, and pushing it into the pool means
     sending a third party's key to the provider's endpoint for a 401 that burns a retry;
   - return the ordered list (possibly empty — the caller decides whether empty is fatal).
5. **Model settings** — `models.json` supplies `stop` and `max_tokens` for known models; unknown
   `--model-name` values get safe defaults. `base_url` is the passed `--provider-url` (or the known
   provider's `base_url` when none is passed); `models.json` is a catalogue, not the source of the
   endpoint.
6. Produce a **`ResolvedConfig`**.

## `ResolvedConfig` (the interface with CORE-1 / CORE-2)

```
provider_name : str          # lowercase, e.g. "groq"
base_url      : str
model_name    : str
stop          : list[str]
max_tokens    : int | None
api_keys      : list[str]     # ordered, de-duplicated; masked in repr
sandbox       : SandboxConfig # from the frozen contract
```

`__repr__`/`__str__` mask `api_keys` (show the count, never the values). This is the single object
SETUP-3 produces; CORE-1 reads `base_url`/`model_name`/`stop`/`max_tokens`, CORE-2 builds its
`KeyPool` from `api_keys`.

## Config files

- **`models.json`** — catalogue keyed by lowercase provider name:
  ```json
  {
    "providers": {
      "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "models": {
          "llama-3.3-70b-versatile": {
            "stop": ["<end_code>", "</tool_call>", "Observation:"],
            "max_tokens": 1500
          }
        }
      },
      "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "qwen/qwen3-235b-a22b-2507",
        "models": {
          "qwen/qwen3-235b-a22b-2507": {
            "stop": ["<end_code>", "</tool_call>", "Observation:"],
            "max_tokens": 1500
          }
        }
      }
    }
  }
  ```
- **`sandbox_template.json`** — our default/dev template matching `SandboxConfig` defaults:
  a standard-library allowlist (`math, cmath, collections, itertools, functools, operator, re, json,
  typing, heapq, bisect, copy, string, random, datetime, array, queue, time, stat, unicodedata`),
  `allowed_directories: ["/testbed", "/tmp/agent"]`, `max_execution_time_seconds: 30`,
  `max_memory_mb: 512`. **This is only our dev default — the grader passes its own `SandboxConfig`
  at runtime, so this list does not need to match the exam.** Final contents to be confirmed with
  Stream A (Eliott), who applies the allowlist.
- **`.env.example`** — committed, documenting the supported variable names with dummy values
  (`GROQ_API_KEY=`, `GROQ_API_KEY_2=`, `GROQ_API_KEYS=`, `OPENROUTER_API_KEY=`, `API_KEY=`). `.env`
  stays gitignored.

## Error handling & security

- `resolve_config` raises `ConfigError` when **no** key is found, and the message lists every
  variable name it tried (names only, never values).
- Missing or malformed `models.json` / `sandbox_template.json` raises `ConfigError` with a readable
  reason.
- Key **values** are never logged, printed, or included in exception messages or `repr`.
- An unparseable `--provider-url` raises `ConfigError` rather than silently defaulting.

## Testing (TDD — tests before implementation)

- **`provider_prefix_from_url`**: overridden host; unknown-host fallback; trailing slash; `http` vs
  `https`; sub-domains; hyphenated domain → legal variable name; unparseable input → error.
- **`discover_api_keys`**: single `_API_KEY`; `_API_KEY_2.._N` across a gap; the scan's upper bound;
  `_API_KEYS` CSV; merge/dedup/order; blanks skipped; another provider's keys ignored; generic
  fallback when nothing prefixed, most specific name winning; empty result.
- **`resolve_config` (the Done-when)**: a Groq URL and an OpenRouter URL both resolve through the
  *same* code path with a monkeypatched env; `ResolvedConfig` carries the right base_url, keys, and
  model settings.
- **Loaders**: valid `models.json` / `sandbox_template.json` load; malformed input raises
  `ConfigError`.
- **Security**: `repr(ResolvedConfig(...))` contains no key value.

## Done when

`resolve_config` loads keys for a Groq URL and an OpenRouter URL with no source change, all tests
pass, and `git log -p | grep -E 'gsk_|sk-'` is empty (no secrets committed).

## Open coordination points

- Final `sandbox_template.json` allowlist contents → confirm with Stream A (Eliott).
- `models.json` model shortlist → will be refined by MBPP-4 (model benchmarking); the values here
  are sensible starting points, not final choices.
