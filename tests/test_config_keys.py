"""Provider prefix derivation and API key discovery.

Both functions are pure: they take the environment as a mapping rather than
reading `os.environ`, so every case here is a plain call with no monkeypatching.
"""

import pytest

from agent_smith.config import ConfigError
from agent_smith.config.keys import discover_api_keys, provider_prefix_from_url


class TestProviderPrefixFromUrl:
    def test_groq_url_yields_the_groq_prefix(self) -> None:
        assert provider_prefix_from_url("https://api.groq.com/openai/v1") == "GROQ"

    def test_openrouter_url_yields_the_openrouter_prefix(self) -> None:
        assert provider_prefix_from_url("https://openrouter.ai/api/v1") == "OPENROUTER"

    def test_scheme_and_trailing_slash_are_irrelevant(self) -> None:
        assert provider_prefix_from_url("http://api.groq.com/openai/v1/") == "GROQ"

    def test_subdomains_do_not_change_the_prefix(self) -> None:
        assert provider_prefix_from_url("https://eu.api.groq.com/openai/v1") == "GROQ"

    def test_a_host_whose_domain_is_not_its_name_is_overridden(self) -> None:
        # The only case the override table earns its keep: the fallback rule
        # would read "GOOGLEAPIS" off this host.
        url = "https://generativelanguage.googleapis.com/v1beta/openai"
        assert provider_prefix_from_url(url) == "GOOGLE"

    def test_unknown_host_falls_back_to_the_second_level_domain(self) -> None:
        assert provider_prefix_from_url("https://api.example.com/v1") == "EXAMPLE"

    def test_hyphens_become_underscores_so_the_prefix_is_a_legal_var_name(self) -> None:
        assert provider_prefix_from_url("https://api.my-llm.io/v1") == "MY_LLM"

    def test_url_without_a_host_is_an_error(self) -> None:
        with pytest.raises(ConfigError):
            provider_prefix_from_url("not a url")

    def test_empty_url_is_an_error(self) -> None:
        with pytest.raises(ConfigError):
            provider_prefix_from_url("")


class TestDiscoverApiKeys:
    def test_single_unsuffixed_key(self) -> None:
        assert discover_api_keys("GROQ", {"GROQ_API_KEY": "k1"}) == ["k1"]

    def test_numbered_keys_follow_the_unsuffixed_one_in_order(self) -> None:
        env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2", "GROQ_API_KEY_3": "k3"}
        assert discover_api_keys("GROQ", env) == ["k1", "k2", "k3"]

    def test_a_gap_does_not_truncate_the_pool(self) -> None:
        # Commenting a key out in `.env` must disable that key, not every key
        # after it: silently shrinking the pool is what starves CORE-2 of keys
        # to rotate through when the rate limits hit.
        env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2", "GROQ_API_KEY_4": "k4"}
        assert discover_api_keys("GROQ", env) == ["k1", "k2", "k4"]

    def test_the_numbered_scan_is_bounded(self) -> None:
        env = {"GROQ_API_KEY_32": "k32", "GROQ_API_KEY_33": "k33"}
        assert discover_api_keys("GROQ", env) == ["k32"]

    def test_numbered_keys_are_found_without_an_unsuffixed_one(self) -> None:
        assert discover_api_keys("GROQ", {"GROQ_API_KEY_2": "k2"}) == ["k2"]

    def test_comma_separated_plural_variable(self) -> None:
        assert discover_api_keys("GROQ", {"GROQ_API_KEYS": "k1,k2 , k3"}) == [
            "k1",
            "k2",
            "k3",
        ]

    def test_all_conventions_merge_in_order_without_duplicates(self) -> None:
        env = {
            "GROQ_API_KEY": "k1",
            "GROQ_API_KEY_2": "k2",
            "GROQ_API_KEYS": "k2,k3",
        }
        assert discover_api_keys("GROQ", env) == ["k1", "k2", "k3"]

    def test_blank_values_are_skipped(self) -> None:
        env = {
            "GROQ_API_KEY": "   ",
            "GROQ_API_KEY_2": "k2",
            "GROQ_API_KEYS": "k3,,  ,k4",
        }
        assert discover_api_keys("GROQ", env) == ["k2", "k3", "k4"]

    def test_another_providers_keys_are_ignored(self) -> None:
        env = {"GROQ_API_KEY": "groq-key", "OPENROUTER_API_KEY": "or-key"}
        assert discover_api_keys("OPENROUTER", env) == ["or-key"]

    def test_generic_names_are_used_when_nothing_is_prefixed(self) -> None:
        assert discover_api_keys("GROQ", {"API_KEY": "env-key"}) == ["env-key"]
        assert discover_api_keys("GROQ", {"LLM_API_KEY": "llm-key"}) == ["llm-key"]

    def test_the_more_specific_generic_name_wins(self) -> None:
        # `LLM_API_KEY` names what the key is for; a bare `API_KEY` in a shared
        # `.env` could belong to anything.
        env = {"API_KEY": "some-other-service", "LLM_API_KEY": "llm-key"}
        assert discover_api_keys("GROQ", env) == ["llm-key"]

    def test_generic_names_are_a_fallback_not_an_addition(self) -> None:
        env = {"GROQ_API_KEY": "k1", "API_KEY": "generic"}
        assert discover_api_keys("GROQ", env) == ["k1"]

    def test_no_key_at_all_returns_an_empty_list(self) -> None:
        assert discover_api_keys("GROQ", {"PATH": "/usr/bin"}) == []
