"""End-to-end configuration resolution.

The Done-when of SETUP-3: a Groq URL and an OpenRouter URL resolve through the
same code path, with no source change.

Note on the fake keys below: none of them start with `gsk_` or `sk-`. The card
is also done-when `git log -p | grep -E 'gsk_|sk-'` comes back empty, and test
fixtures live in git like everything else — realistic-looking dummy keys would
trip our own secret check for the rest of the project's life.
"""

import json
from pathlib import Path

import pytest

from agent_smith.config import ConfigError, ResolvedConfig, resolve_config

MODELS = {
    "providers": {
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "default_model": "llama-3.3-70b-versatile",
            "models": {
                "llama-3.3-70b-versatile": {"stop": ["<end_code>"], "max_tokens": 1500},
            },
        },
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "qwen/qwen3-235b-a22b-2507",
            "models": {
                "qwen/qwen3-235b-a22b-2507": {
                    "stop": ["Observation:"],
                    "max_tokens": 900,
                },
            },
        },
    },
}

SANDBOX = {
    "authorized_imports": ["math", "json"],
    "allowed_directories": ["/testbed"],
    "max_execution_time_seconds": 30,
    "max_memory_mb": 512,
}


@pytest.fixture
def config_files(tmp_path: Path) -> tuple[Path, Path]:
    models = tmp_path / "models.json"
    models.write_text(json.dumps(MODELS), encoding="utf-8")
    sandbox = tmp_path / "sandbox_template.json"
    sandbox.write_text(json.dumps(SANDBOX), encoding="utf-8")
    return models, sandbox


def resolve(
    config_files: tuple[Path, Path],
    env: dict[str, str],
    **kwargs: object,
) -> ResolvedConfig:
    models, sandbox = config_files
    return resolve_config(
        models_path=models,
        sandbox_path=sandbox,
        env=env,
        **kwargs,  # type: ignore[arg-type]
    )


class TestTheSameCodePathServesEveryProvider:
    def test_a_groq_url_resolves(self, config_files: tuple[Path, Path]) -> None:
        config = resolve(
            config_files,
            {"GROQ_API_KEY": "key-1", "GROQ_API_KEY_2": "key-2"},
            provider_url="https://api.groq.com/openai/v1",
        )

        assert config.provider_name == "groq"
        assert config.base_url == "https://api.groq.com/openai/v1"
        assert config.model_name == "llama-3.3-70b-versatile"
        assert config.stop == ["<end_code>"]
        assert config.max_tokens == 1500
        assert config.api_keys == ["key-1", "key-2"]

    def test_an_openrouter_url_resolves_the_same_way(
        self, config_files: tuple[Path, Path]
    ) -> None:
        config = resolve(
            config_files,
            {"OPENROUTER_API_KEY": "key-or"},
            provider_url="https://openrouter.ai/api/v1",
        )

        assert config.provider_name == "openrouter"
        assert config.base_url == "https://openrouter.ai/api/v1"
        assert config.model_name == "qwen/qwen3-235b-a22b-2507"
        assert config.stop == ["Observation:"]
        assert config.max_tokens == 900
        assert config.api_keys == ["key-or"]

    def test_an_endpoint_we_never_heard_of_resolves_with_safe_defaults(
        self, config_files: tuple[Path, Path]
    ) -> None:
        config = resolve(
            config_files,
            {"NEWLLM_API_KEY": "key-new"},
            provider_url="https://api.newllm.io/v1",
            model_name="some-model",
        )

        assert config.provider_name == "newllm"
        assert config.base_url == "https://api.newllm.io/v1"
        assert config.model_name == "some-model"
        assert config.stop == []
        assert config.max_tokens is None
        assert config.api_keys == ["key-new"]


class TestDefaults:
    def test_groq_is_the_development_default_when_no_url_is_passed(
        self, config_files: tuple[Path, Path]
    ) -> None:
        config = resolve(config_files, {"GROQ_API_KEY": "key-1"})

        assert config.provider_name == "groq"
        assert config.base_url == "https://api.groq.com/openai/v1"

    def test_an_unknown_model_on_a_known_provider_keeps_the_provider_endpoint(
        self, config_files: tuple[Path, Path]
    ) -> None:
        config = resolve(
            config_files,
            {"GROQ_API_KEY": "key-1"},
            provider_url="https://api.groq.com/openai/v1",
            model_name="llama-4-未来",
        )

        assert config.model_name == "llama-4-未来"
        assert config.stop == []
        assert config.max_tokens is None

    def test_an_unknown_provider_without_a_model_name_is_an_error(
        self, config_files: tuple[Path, Path]
    ) -> None:
        with pytest.raises(ConfigError, match="model"):
            resolve(
                config_files,
                {"NEWLLM_API_KEY": "key-new"},
                provider_url="https://api.newllm.io/v1",
            )

    def test_the_sandbox_template_is_loaded_into_the_frozen_contract(
        self, config_files: tuple[Path, Path]
    ) -> None:
        config = resolve(config_files, {"GROQ_API_KEY": "key-1"})

        assert config.sandbox.authorized_imports == ["math", "json"]
        assert config.sandbox.allowed_directories == ["/testbed"]


class TestMissingKeys:
    def test_no_key_at_all_is_fatal(self, config_files: tuple[Path, Path]) -> None:
        with pytest.raises(ConfigError):
            resolve(config_files, {"PATH": "/usr/bin"})

    def test_the_error_names_every_variable_it_looked_for(
        self, config_files: tuple[Path, Path]
    ) -> None:
        with pytest.raises(ConfigError) as raised:
            resolve(config_files, {"PATH": "/usr/bin"})

        message = str(raised.value)
        assert "GROQ_API_KEY" in message
        assert "GROQ_API_KEYS" in message
        assert "LLM_API_KEY" in message


class TestKeysAreNeverPrinted:
    def test_repr_masks_the_keys(self, config_files: tuple[Path, Path]) -> None:
        config = resolve(
            config_files,
            {"GROQ_API_KEY": "key-1", "GROQ_API_KEY_2": "key-2"},
        )

        assert "key-1" not in repr(config)
        assert "key-2" not in repr(config)
        assert "2 keys" in repr(config)

    def test_str_masks_the_keys(self, config_files: tuple[Path, Path]) -> None:
        config = resolve(config_files, {"GROQ_API_KEY": "key-1"})

        assert "key-1" not in str(config)

    def test_the_keys_are_still_reachable_for_the_key_pool(
        self, config_files: tuple[Path, Path]
    ) -> None:
        # Masking is about accidental printing, not about hiding the values
        # from CORE-2, which needs them to build its rotation pool.
        config = resolve(config_files, {"GROQ_API_KEY": "key-1"})

        assert config.api_keys == ["key-1"]
