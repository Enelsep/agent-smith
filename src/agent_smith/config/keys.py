"""Provider identification and API key discovery.

We do not control the `.env` we are handed at evaluation time, nor the names of
the variables in it. So the provider is derived from `--provider-url` rather
than hardcoded, and the keys are looked up under several conventions.

Both functions are pure — the environment comes in as a mapping — which is what
makes the whole key-resolution path testable without touching `os.environ`.
"""

from collections.abc import Mapping
from urllib.parse import urlparse

from agent_smith.config.errors import ConfigError

# Overrides, not a support list: a host only belongs here when the fallback
# below gets it wrong. `api.groq.com` and `openrouter.ai` already yield GROQ and
# OPENROUTER on their own, so listing them would only suggest we endorse some
# providers over others — the opposite of what this module is for. Google is
# here because its endpoint lives on googleapis.com, which reads as GOOGLEAPIS.
# Matched as a suffix, so subdomains resolve like their parent domain.
_PREFIX_OVERRIDES: dict[str, str] = {
    "googleapis.com": "GOOGLE",
}

# Consulted only when nothing prefixed was found, most specific name first: a
# bare `API_KEY` in a `.env` we did not write could belong to any service.
_GENERIC_KEY_NAMES = ("LLM_API_KEY", "API_KEY")

# The numbered scan tolerates holes, so it needs a bound rather than a stop
# condition. Nobody carries thirty-two keys; this is only here to terminate.
_MAX_NUMBERED_KEYS = 32


def provider_prefix_from_url(url: str) -> str:
    """Return the environment-variable prefix for the provider behind `url`.

    Raises `ConfigError` if no host can be read out of the URL, rather than
    silently falling back to a default provider.
    """
    host = urlparse(url).hostname
    if not host:
        raise ConfigError(f"cannot read a host out of the provider URL: {url!r}")

    for domain, prefix in _PREFIX_OVERRIDES.items():
        if host == domain or host.endswith(f".{domain}"):
            return prefix

    parts = host.split(".")
    second_level = parts[-2] if len(parts) >= 2 else parts[0]
    return second_level.replace("-", "_").upper()


def discover_api_keys(prefix: str, env: Mapping[str, str]) -> list[str]:
    """Collect every API key `env` holds for `prefix`, in preference order.

    Looks under `<PREFIX>_API_KEY`, then `<PREFIX>_API_KEY_2..N`, then a
    comma-separated `<PREFIX>_API_KEYS`. Generic names are consulted only when
    nothing prefixed was found. The list may be empty; it is up to the caller
    to decide whether that is fatal.
    """
    keys: list[str] = []

    _append(keys, env.get(f"{prefix}_API_KEY"))

    for index in range(2, _MAX_NUMBERED_KEYS + 1):
        _append(keys, env.get(f"{prefix}_API_KEY_{index}"))

    for part in (env.get(f"{prefix}_API_KEYS") or "").split(","):
        _append(keys, part)

    if not keys:
        for name in _GENERIC_KEY_NAMES:
            _append(keys, env.get(name))
            if keys:
                break

    return keys


def _append(keys: list[str], value: str | None) -> None:
    """Append `value` stripped, unless it is blank or already collected."""
    if value is None:
        return
    candidate = value.strip()
    if candidate and candidate not in keys:
        keys.append(candidate)
