import ipaddress
from collections.abc import Mapping
from urllib.parse import urlparse

from agent_smith.config.errors import ConfigError

_LOCAL_PREFIX = "LOCAL"
_PREFIX_OVERRIDES: dict[str, str] = {
    "googleapis.com": "GOOGLE",
}
_GENERIC_KEY_NAMES = ("LLM_API_KEY", "API_KEY")
_MAX_NUMBERED_KEYS = 32


def provider_prefix_from_url(url: str) -> str:
    """Return the environment-variable prefix for the provider behind `url`.

    Raises `ConfigError` if no host can be read out of the URL, rather than
    silently falling back to a default provider.
    """
    host = urlparse(url).hostname
    if not host:
        raise ConfigError(f"cannot read a host out of the provider URL: {url!r}")

    if _is_address(host):
        return _LOCAL_PREFIX

    for domain, prefix in _PREFIX_OVERRIDES.items():
        if host == domain or host.endswith(f".{domain}"):
            return prefix

    parts = host.split(".")
    second_level = parts[-2] if len(parts) >= 2 else parts[0]
    return second_level.replace("-", "_").upper()


def prefixed_api_keys(prefix: str, env: Mapping[str, str]) -> list[str]:
    """Collect the keys `env` holds under `prefix`'s own names, in order.

    Looks under `<PREFIX>_API_KEY`, then `<PREFIX>_API_KEY_2..N`, then a
    comma-separated `<PREFIX>_API_KEYS`. Nothing generic: a key found here
    names its provider, which is what makes a non-empty result usable as
    evidence that this provider is the one the caller meant.
    """
    keys: list[str] = []

    _append(keys, env.get(f"{prefix}_API_KEY"))

    for index in range(2, _MAX_NUMBERED_KEYS + 1):
        _append(keys, env.get(f"{prefix}_API_KEY_{index}"))

    for part in (env.get(f"{prefix}_API_KEYS") or "").split(","):
        _append(keys, part)

    return keys


def discover_api_keys(prefix: str, env: Mapping[str, str]) -> list[str]:
    """Collect every API key `env` holds for `prefix`, in preference order.

    `prefixed_api_keys` first; generic names are consulted only when nothing
    prefixed was found. The list may be empty; it is up to the caller to
    decide whether that is fatal.
    """
    keys = prefixed_api_keys(prefix, env)

    if not keys:
        for name in _GENERIC_KEY_NAMES:
            _append(keys, env.get(name))
            if keys:
                break

    return keys


def candidate_key_names(prefix: str) -> list[str]:
    """Name every variable `discover_api_keys` consults, for error messages.

    The numbered range is summarised rather than expanded: thirty-two names in
    a stderr line help nobody.
    """
    return [
        f"{prefix}_API_KEY",
        f"{prefix}_API_KEY_2 … {prefix}_API_KEY_{_MAX_NUMBERED_KEYS}",
        f"{prefix}_API_KEYS",
        *_GENERIC_KEY_NAMES,
    ]


def _is_address(host: str) -> bool:
    """True when the host names a machine rather than a provider."""
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _append(keys: list[str], value: str | None) -> None:
    """Append `value` stripped, unless it is blank or already collected."""
    if value is None:
        return
    candidate = value.strip()
    if candidate and candidate not in keys:
        keys.append(candidate)
