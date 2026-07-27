# SETUP-3 — Config files & env loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `--provider-url` / `--model-name` / the supplied `.env` / the JSON config files into one validated `ResolvedConfig`, with provider-agnostic API-key discovery.

**Architecture:** A small `src/agent_smith/config/` package. Pure logic (`keys.py`) is separated from I/O (`loader.py`) and orchestration (`resolve.py`); internal Pydantic models for `models.json` live in `schema.py`; the frozen contract's `SandboxConfig` is reused unchanged. SETUP-3 only *discovers* keys as an ordered list — rotation is CORE-2.

**Tech Stack:** Python ≥3.10, Pydantic v2, `python-dotenv`, `pytest`, stdlib `urllib.parse`.

## Global Constraints

- Python ≥ 3.10; existing `src/` layout, packages already declared in `pyproject.toml`.
- Provider identity is **derived from `--provider-url`**, never a hardcoded constant. Groq is the dev default only when no URL is passed.
- Key **values** never appear in logs, `repr`, or exception messages. `git log -p | grep -E 'gsk_|sk-'` must stay empty.
- Do **not** modify `src/agent_smith/models/contract.py` (frozen). Reuse `SandboxConfig` from it.
- `.env` stays gitignored; only `.env.example` is committed.
- TDD throughout: test first, minimal implementation, frequent commits. `ruff` + `pytest` green.

## File Structure

- `src/agent_smith/config/__init__.py` — package + public re-exports (`resolve_config`, `ResolvedConfig`, `ConfigError`).
- `src/agent_smith/config/keys.py` — pure: `provider_prefix_from_url`, `discover_api_keys`.
- `src/agent_smith/config/schema.py` — Pydantic models for `models.json`.
- `src/agent_smith/config/errors.py` — `ConfigError`.
- `src/agent_smith/config/loader.py` — `load_models_config`, `load_sandbox_config`.
- `src/agent_smith/config/resolve.py` — `ResolvedConfig`, `resolve_config`.
- `models.json`, `sandbox_template.json` — filled from empty stubs.
- `.env.example` — created. `pyproject.toml` — add `python-dotenv`.
- `tests/config/test_keys.py`, `test_loader.py`, `test_resolve.py`.

---

### Task 1: `provider_prefix_from_url` (pure)

**Files:**
- Create: `src/agent_smith/config/__init__.py` (empty for now)
- Create: `src/agent_smith/config/keys.py`
- Test: `tests/config/test_keys.py`

**Interfaces:**
- Produces: `provider_prefix_from_url(url: str) -> str` (uppercase prefix; raises `ValueError` on an unparseable/empty host).

- [ ] **Step 1: Write the failing tests**

```python
# tests/config/test_keys.py
import pytest
from agent_smith.config.keys import provider_prefix_from_url


@pytest.mark.parametrize("url, expected", [
    ("https://api.groq.com/openai/v1", "GROQ"),
    ("https://api.groq.com/openai/v1/", "GROQ"),        # trailing slash
    ("http://api.groq.com/openai/v1", "GROQ"),          # http
    ("api.groq.com/openai/v1", "GROQ"),                 # no scheme
    ("https://openrouter.ai/api/v1", "OPENROUTER"),
    ("https://api.together.xyz/v1", "TOGETHER"),
    ("https://api.mistral.ai/v1", "MISTRAL"),
    ("https://ai.google.dev/v1", "GOOGLE"),             # google special-case
    ("https://foo.example.com/v1", "EXAMPLE"),          # unknown -> 2nd-level domain
])
def test_provider_prefix_from_url(url, expected):
    assert provider_prefix_from_url(url) == expected


def test_provider_prefix_rejects_empty():
    with pytest.raises(ValueError):
        provider_prefix_from_url("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_keys.py -q`
Expected: FAIL (`ModuleNotFoundError: agent_smith.config.keys`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/agent_smith/config/keys.py
from urllib.parse import urlparse

_KNOWN_HOSTS = {
    "api.groq.com": "GROQ",
    "openrouter.ai": "OPENROUTER",
    "api.together.xyz": "TOGETHER",
    "api.together.ai": "TOGETHER",
    "api.mistral.ai": "MISTRAL",
    "api.openai.com": "OPENAI",
}


def provider_prefix_from_url(url: str) -> str:
    """Derive the env-var prefix (e.g. 'GROQ') from a provider base URL."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"cannot parse provider url: {url!r}")
    if host in _KNOWN_HOSTS:
        return _KNOWN_HOSTS[host]
    if "google" in host:
        return "GOOGLE"
    parts = host.split(".")
    return (parts[-2] if len(parts) >= 2 else parts[-1]).upper()
```

Also create empty `src/agent_smith/config/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_keys.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agent_smith/config/__init__.py src/agent_smith/config/keys.py tests/config/test_keys.py
git commit -m "feat(config): derive provider prefix from provider URL (AGE-7)"
```

---

### Task 2: `discover_api_keys` (pure)

**Files:**
- Modify: `src/agent_smith/config/keys.py`
- Test: `tests/config/test_keys.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `discover_api_keys(prefix: str, env: Mapping[str, str]) -> list[str]` — ordered, de-duplicated, blanks skipped; prefixed keys first (`<PREFIX>_API_KEY`, then `_API_KEY_2.._N` until the first gap, then `<PREFIX>_API_KEYS` CSV); if nothing prefixed, fall back to `API_KEY` then `LLM_API_KEY`; may return `[]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/config/test_keys.py
from agent_smith.config.keys import discover_api_keys


def test_single_prefixed_key():
    assert discover_api_keys("GROQ", {"GROQ_API_KEY": "a"}) == ["a"]


def test_numbered_keys_in_order():
    env = {"GROQ_API_KEY": "a", "GROQ_API_KEY_2": "b", "GROQ_API_KEY_3": "c"}
    assert discover_api_keys("GROQ", env) == ["a", "b", "c"]


def test_numbered_keys_stop_at_first_gap():
    env = {"GROQ_API_KEY": "a", "GROQ_API_KEY_3": "c"}  # _2 missing
    assert discover_api_keys("GROQ", env) == ["a"]


def test_csv_keys():
    assert discover_api_keys("GROQ", {"GROQ_API_KEYS": "x, y ,z"}) == ["x", "y", "z"]


def test_merge_and_dedup_preserves_order():
    env = {"GROQ_API_KEY": "a", "GROQ_API_KEYS": "a,b"}
    assert discover_api_keys("GROQ", env) == ["a", "b"]


def test_generic_fallback_when_nothing_prefixed():
    assert discover_api_keys("GROQ", {"API_KEY": "g"}) == ["g"]


def test_prefixed_wins_over_generic():
    assert discover_api_keys("GROQ", {"GROQ_API_KEY": "a", "API_KEY": "g"}) == ["a"]


def test_empty_when_no_keys():
    assert discover_api_keys("GROQ", {}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_keys.py -k discover -q`
Expected: FAIL (`ImportError: cannot import name 'discover_api_keys'`).

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/agent_smith/config/keys.py
from collections.abc import Mapping

_GENERIC_NAMES = ("API_KEY", "LLM_API_KEY")


def discover_api_keys(prefix: str, env: Mapping[str, str]) -> list[str]:
    """Discover an ordered, de-duplicated list of API keys for a provider prefix."""
    keys: list[str] = []

    def add(raw: str) -> None:
        for part in raw.split(","):
            k = part.strip()
            if k and k not in keys:
                keys.append(k)

    if env.get(f"{prefix}_API_KEY", "").strip():
        add(env[f"{prefix}_API_KEY"])

    i = 2
    while env.get(f"{prefix}_API_KEY_{i}", "").strip():
        add(env[f"{prefix}_API_KEY_{i}"])
        i += 1

    if env.get(f"{prefix}_API_KEYS", "").strip():
        add(env[f"{prefix}_API_KEYS"])

    if not keys:
        for name in _GENERIC_NAMES:
            if env.get(name, "").strip():
                add(env[name])

    return keys
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_keys.py -q`
Expected: PASS (all keys tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_smith/config/keys.py tests/config/test_keys.py
git commit -m "feat(config): multi-convention API key discovery with generic fallback (AGE-7)"
```

---

### Task 3: `models.json` schema + loader

**Files:**
- Create: `src/agent_smith/config/schema.py`
- Create: `src/agent_smith/config/errors.py`
- Create: `src/agent_smith/config/loader.py`
- Modify: `models.json` (fill the empty stub)
- Test: `tests/config/test_loader.py`

**Interfaces:**
- Produces: `ModelConfig(stop: list[str], max_tokens: int | None)`, `ProviderConfig(base_url: str, default_model: str | None, models: dict[str, ModelConfig])`, `ModelsConfig(providers: dict[str, ProviderConfig])`; `ConfigError(Exception)`; `load_models_config(path) -> ModelsConfig` (raises `ConfigError` on missing/malformed).

- [ ] **Step 1: Write the failing tests**

```python
# tests/config/test_loader.py
import pytest
from agent_smith.config.errors import ConfigError
from agent_smith.config.loader import load_models_config


def test_loads_real_models_json():
    cfg = load_models_config("models.json")
    assert "groq" in cfg.providers
    assert cfg.providers["groq"].base_url.startswith("https://")


def test_missing_models_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_models_config(tmp_path / "nope.json")


def test_malformed_models_file_raises_config_error(tmp_path):
    bad = tmp_path / "models.json"
    bad.write_text('{"providers": {"groq": {}}}')  # missing required base_url
    with pytest.raises(ConfigError):
        load_models_config(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_loader.py -q`
Expected: FAIL (`ModuleNotFoundError: agent_smith.config.errors`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/agent_smith/config/errors.py
class ConfigError(Exception):
    """Raised when configuration cannot be loaded or resolved."""
```

```python
# src/agent_smith/config/schema.py
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    stop: list[str] = Field(default_factory=list)
    max_tokens: int | None = None


class ProviderConfig(BaseModel):
    base_url: str
    default_model: str | None = None
    models: dict[str, ModelConfig] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
```

```python
# src/agent_smith/config/loader.py
from pathlib import Path

from pydantic import ValidationError

from agent_smith.config.errors import ConfigError
from agent_smith.config.schema import ModelsConfig
from agent_smith.models.contract import SandboxConfig


def load_models_config(path="models.json") -> ModelsConfig:
    p = Path(path)
    try:
        return ModelsConfig.model_validate_json(p.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"models config not found: {p}") from e
    except (ValidationError, ValueError) as e:
        raise ConfigError(f"invalid models config {p}: {e}") from e


def load_sandbox_config(path="sandbox_template.json") -> SandboxConfig:
    p = Path(path)
    try:
        return SandboxConfig.model_validate_json(p.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"sandbox config not found: {p}") from e
    except (ValidationError, ValueError) as e:
        raise ConfigError(f"invalid sandbox config {p}: {e}") from e
```

Fill `models.json`:

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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_loader.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agent_smith/config/errors.py src/agent_smith/config/schema.py src/agent_smith/config/loader.py models.json tests/config/test_loader.py
git commit -m "feat(config): models.json schema + loader with ConfigError (AGE-7)"
```

---

### Task 4: sandbox loader + `sandbox_template.json`

**Files:**
- Modify: `sandbox_template.json` (fill the empty stub)
- Test: `tests/config/test_loader.py`

**Interfaces:**
- Consumes: `load_sandbox_config(path) -> SandboxConfig` (added in Task 3).
- Produces: a valid default `sandbox_template.json`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/config/test_loader.py
from agent_smith.config.loader import load_sandbox_config


def test_loads_real_sandbox_template():
    cfg = load_sandbox_config("sandbox_template.json")
    assert "/tmp/agent" in cfg.allowed_directories
    assert "math" in cfg.authorized_imports
    assert cfg.max_execution_time_seconds == 30
    assert cfg.max_memory_mb == 512
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_loader.py::test_loads_real_sandbox_template -q`
Expected: FAIL (empty `sandbox_template.json` → `ConfigError`).

- [ ] **Step 3: Fill `sandbox_template.json`**

```json
{
  "authorized_imports": [
    "math", "cmath", "collections", "itertools", "functools", "operator",
    "re", "json", "typing", "heapq", "bisect", "copy", "string", "random",
    "datetime", "array", "queue", "time", "stat", "unicodedata"
  ],
  "allowed_directories": ["/testbed", "/tmp/agent"],
  "max_execution_time_seconds": 30,
  "max_memory_mb": 512
}
```

> Note: this is our dev default; the grader passes its own `SandboxConfig` at runtime. Final allowlist to be confirmed with Stream A (Eliott).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_loader.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add sandbox_template.json tests/config/test_loader.py
git commit -m "feat(config): default sandbox_template.json + loader test (AGE-7)"
```

---

### Task 5: `resolve_config` + `ResolvedConfig` (the Done-when)

**Files:**
- Modify: `pyproject.toml` (add `python-dotenv`)
- Create: `src/agent_smith/config/resolve.py`
- Modify: `src/agent_smith/config/__init__.py` (re-exports)
- Create: `.env.example`
- Test: `tests/config/test_resolve.py`

**Interfaces:**
- Consumes: `provider_prefix_from_url`, `discover_api_keys` (Task 1-2); `load_models_config`, `load_sandbox_config`, `ConfigError` (Task 3-4).
- Produces: `ResolvedConfig(provider_name, base_url, model_name, stop, max_tokens, api_keys, sandbox)` with key-masking `__repr__`; `resolve_config(provider_url=None, model_name=None, *, env=None, models_path="models.json", sandbox_path="sandbox_template.json", load_env=True) -> ResolvedConfig`.

- [ ] **Step 1: Add the dependency**

Add `"python-dotenv"` to `dependencies` in `pyproject.toml`, then:

Run: `uv sync -q`
Expected: `python-dotenv` installed.

- [ ] **Step 2: Write the failing tests**

```python
# tests/config/test_resolve.py
import pytest
from agent_smith.config import ConfigError, ResolvedConfig, resolve_config


def test_groq_and_openrouter_same_code_path():
    groq = resolve_config(
        "https://api.groq.com/openai/v1",
        env={"GROQ_API_KEY": "g1"}, load_env=False,
    )
    openr = resolve_config(
        "https://openrouter.ai/api/v1",
        env={"OPENROUTER_API_KEY": "o1"}, load_env=False,
    )
    assert groq.provider_name == "groq"
    assert groq.base_url == "https://api.groq.com/openai/v1"
    assert groq.api_keys == ["g1"]
    assert groq.stop and groq.max_tokens == 1500
    assert openr.provider_name == "openrouter"
    assert openr.api_keys == ["o1"]


def test_default_provider_is_groq_when_no_url():
    cfg = resolve_config(env={"GROQ_API_KEY": "g1"}, load_env=False)
    assert cfg.provider_name == "groq"
    assert cfg.model_name == "llama-3.3-70b-versatile"


def test_no_key_raises_config_error_without_leaking():
    with pytest.raises(ConfigError) as exc:
        resolve_config("https://api.groq.com/openai/v1", env={}, load_env=False)
    assert "GROQ_API_KEY" in str(exc.value)


def test_repr_masks_keys():
    cfg = resolve_config(
        "https://api.groq.com/openai/v1",
        env={"GROQ_API_KEY": "supersecret"}, load_env=False,
    )
    assert "supersecret" not in repr(cfg)
    assert "1 key" in repr(cfg)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/config/test_resolve.py -q`
Expected: FAIL (`ImportError` from `agent_smith.config`).

- [ ] **Step 4: Write minimal implementation**

```python
# src/agent_smith/config/resolve.py
import os
from collections.abc import Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agent_smith.config.errors import ConfigError
from agent_smith.config.keys import discover_api_keys, provider_prefix_from_url
from agent_smith.config.loader import load_models_config, load_sandbox_config
from agent_smith.models.contract import SandboxConfig

DEFAULT_PROVIDER_URL = "https://api.groq.com/openai/v1"
DEFAULT_STOP = ["<end_code>", "</tool_call>", "Observation:"]


class ResolvedConfig(BaseModel):
    provider_name: str
    base_url: str
    model_name: str
    stop: list[str] = Field(default_factory=list)
    max_tokens: int | None = None
    api_keys: list[str] = Field(default_factory=list)
    sandbox: SandboxConfig

    def __repr__(self) -> str:
        return (
            f"ResolvedConfig(provider_name={self.provider_name!r}, "
            f"base_url={self.base_url!r}, model_name={self.model_name!r}, "
            f"stop={self.stop!r}, max_tokens={self.max_tokens!r}, "
            f"api_keys=<{len(self.api_keys)} key(s)>, sandbox=<...>)"
        )

    __str__ = __repr__


def resolve_config(
    provider_url: str | None = None,
    model_name: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    models_path="models.json",
    sandbox_path="sandbox_template.json",
    load_env: bool = True,
) -> ResolvedConfig:
    if load_env:
        load_dotenv()
    env = os.environ if env is None else env

    url = provider_url or DEFAULT_PROVIDER_URL
    try:
        prefix = provider_prefix_from_url(url)
    except ValueError as e:
        raise ConfigError(str(e)) from e

    keys = discover_api_keys(prefix, env)
    if not keys:
        tried = [f"{prefix}_API_KEY", f"{prefix}_API_KEY_2..N", f"{prefix}_API_KEYS", "API_KEY", "LLM_API_KEY"]
        raise ConfigError(f"no API key found for provider {prefix}; looked for: {', '.join(tried)}")

    models = load_models_config(models_path)
    provider_name = prefix.lower()
    provider = models.providers.get(provider_name)

    base_url = provider_url or (provider.base_url if provider else DEFAULT_PROVIDER_URL)
    chosen_model = model_name or (provider.default_model if provider else None) or ""
    model_cfg = provider.models.get(chosen_model) if provider else None
    stop = list(model_cfg.stop) if (model_cfg and model_cfg.stop) else list(DEFAULT_STOP)
    max_tokens = model_cfg.max_tokens if model_cfg else None

    sandbox = load_sandbox_config(sandbox_path)

    return ResolvedConfig(
        provider_name=provider_name,
        base_url=base_url,
        model_name=chosen_model,
        stop=stop,
        max_tokens=max_tokens,
        api_keys=keys,
        sandbox=sandbox,
    )
```

```python
# src/agent_smith/config/__init__.py
from agent_smith.config.errors import ConfigError
from agent_smith.config.keys import discover_api_keys, provider_prefix_from_url
from agent_smith.config.resolve import ResolvedConfig, resolve_config

__all__ = [
    "ConfigError",
    "ResolvedConfig",
    "resolve_config",
    "provider_prefix_from_url",
    "discover_api_keys",
]
```

Create `.env.example`:

```bash
# Copy to .env (gitignored) and fill in. Keys are discovered by the provider
# prefix derived from --provider-url. Multiple keys per provider are supported.
GROQ_API_KEY=
GROQ_API_KEY_2=
GROQ_API_KEYS=
OPENROUTER_API_KEY=
# Generic fallback, used only if no provider-prefixed key is found:
API_KEY=
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/config/ -q`
Expected: PASS (all config tests).

- [ ] **Step 6: Full suite + lint**

Run: `uv run pytest -q && uv run ruff check src/agent_smith/config`
Expected: all green, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/agent_smith/config/resolve.py src/agent_smith/config/__init__.py .env.example tests/config/test_resolve.py
git commit -m "feat(config): resolve_config + ResolvedConfig with masked keys (AGE-7)"
```

---

## Self-Review

**Spec coverage:** provider derivation (Task 1) ✔; multi-convention key discovery + generic fallback (Task 2) ✔; models.json schema/loader + ConfigError (Task 3) ✔; sandbox_template.json + loader (Task 4) ✔; ResolvedConfig, dotenv, .env.example, Done-when Groq+OpenRouter same path, repr masking (Task 5) ✔. No uncovered spec section.

**Placeholders:** none — every code and test step is complete.

**Type consistency:** `provider_prefix_from_url`/`discover_api_keys` signatures match between Task 1-2 and their use in Task 5; `load_models_config`/`load_sandbox_config`/`ConfigError` defined in Task 3 and consumed in Task 4-5; `ResolvedConfig` field names match the spec and the assertions.
