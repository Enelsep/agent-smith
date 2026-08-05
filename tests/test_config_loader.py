"""Loading and validating `models.json` and `sandbox_template.json`."""

import json
from pathlib import Path

import pytest

from agent_smith.config import ConfigError
from agent_smith.config.loader import load_models_config, load_sandbox_config

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_MODELS = {
    "providers": {
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "default_model": "llama-3.3-70b-versatile",
            "models": {
                "llama-3.3-70b-versatile": {"stop": ["<end_code>"], "max_tokens": 1500},
            },
        },
    },
}


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestLoadModelsConfig:
    def test_reads_a_provider_and_its_models(self, tmp_path: Path) -> None:
        config = load_models_config(write_json(tmp_path / "models.json", VALID_MODELS))

        provider = config.providers["groq"]
        assert provider.base_url == "https://api.groq.com/openai/v1"
        assert provider.default_model == "llama-3.3-70b-versatile"
        assert provider.models["llama-3.3-70b-versatile"].stop == ["<end_code>"]
        assert provider.models["llama-3.3-70b-versatile"].max_tokens == 1500

    def test_model_settings_are_optional(self, tmp_path: Path) -> None:
        payload = {
            "providers": {
                "groq": {
                    "base_url": "https://api.groq.com/openai/v1",
                    "default_model": "m",
                    "models": {"m": {}},
                },
            },
        }
        config = load_models_config(write_json(tmp_path / "models.json", payload))

        assert config.providers["groq"].models["m"].stop == []
        assert config.providers["groq"].models["m"].max_tokens is None

    def test_missing_file_is_a_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_models_config(tmp_path / "absent.json")

    def test_malformed_json_is_a_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(ConfigError):
            load_models_config(path)

    def test_missing_required_field_is_a_config_error(self, tmp_path: Path) -> None:
        payload = {"providers": {"groq": {"default_model": "m", "models": {}}}}

        with pytest.raises(ConfigError):
            load_models_config(write_json(tmp_path / "models.json", payload))

    def test_an_unknown_field_is_rejected_so_typos_do_not_pass_silently(
        self, tmp_path: Path
    ) -> None:
        # `max_token` instead of `max_tokens` would otherwise leave the model
        # on its default budget, and nothing would say so.
        payload = {
            "providers": {
                "groq": {
                    "base_url": "https://api.groq.com/openai/v1",
                    "default_model": "m",
                    "models": {"m": {"max_token": 1500}},
                },
            },
        }

        with pytest.raises(ConfigError):
            load_models_config(write_json(tmp_path / "models.json", payload))

    @pytest.mark.parametrize("budget", [0, -1])
    def test_an_impossible_token_budget_is_rejected_at_load(
        self, tmp_path: Path, budget: int
    ) -> None:
        # `None` already means "leave the provider's default in force", so a
        # zero has no valid reading. Left to stand it would reach the endpoint
        # as `max_tokens: 0` — a 400 raised mid-run, pointing at the network
        # layer rather than at the line that caused it.
        payload = {
            "providers": {
                "groq": {
                    "base_url": "https://api.groq.com/openai/v1",
                    "default_model": "m",
                    "models": {"m": {"max_tokens": budget}},
                },
            },
        }

        with pytest.raises(ConfigError):
            load_models_config(write_json(tmp_path / "models.json", payload))

    def test_the_error_names_the_file_it_could_not_read(self, tmp_path: Path) -> None:
        path = tmp_path / "models.json"
        path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(ConfigError, match="models.json"):
            load_models_config(path)


class TestLoadSandboxConfig:
    def test_returns_the_frozen_contracts_sandbox_config(self, tmp_path: Path) -> None:
        payload = {
            "authorized_imports": ["math", "json"],
            "allowed_directories": ["/testbed"],
            "max_execution_time_seconds": 15,
            "max_memory_mb": 256,
        }
        config = load_sandbox_config(write_json(tmp_path / "sandbox.json", payload))

        assert config.authorized_imports == ["math", "json"]
        assert config.allowed_directories == ["/testbed"]
        assert config.max_execution_time_seconds == 15
        assert config.max_memory_mb == 256

    def test_missing_file_is_a_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_sandbox_config(tmp_path / "absent.json")

    def test_wrong_field_type_is_a_config_error(self, tmp_path: Path) -> None:
        payload = {"max_memory_mb": "a lot"}

        with pytest.raises(ConfigError):
            load_sandbox_config(write_json(tmp_path / "sandbox.json", payload))

    def test_an_unknown_field_is_rejected_so_typos_do_not_pass_silently(
        self, tmp_path: Path
    ) -> None:
        # `SandboxConfig` belongs to the frozen contract, so we cannot give it
        # `extra="forbid"`. Without a guard here, `authorized_import` validates
        # cleanly and the sandbox runs with an empty allowlist — every import
        # refused, and nothing said why.
        payload = {"authorized_import": ["math"], "allowed_directories": ["/testbed"]}

        with pytest.raises(ConfigError, match="authorized_import"):
            load_sandbox_config(write_json(tmp_path / "sandbox.json", payload))


class TestCommittedConfigFiles:
    """The files we ship must stay loadable — they are the defaults we run with."""

    def test_repository_models_json_is_valid(self) -> None:
        config = load_models_config(REPO_ROOT / "models.json")

        assert "groq" in config.providers
        assert "openrouter" in config.providers

    def test_every_catalogued_provider_declares_its_default_model(self) -> None:
        config = load_models_config(REPO_ROOT / "models.json")

        for name, provider in config.providers.items():
            assert provider.default_model in provider.models, (
                f"{name}: default_model is not in its own models table"
            )

    def test_repository_sandbox_template_is_valid(self) -> None:
        config = load_sandbox_config(REPO_ROOT / "sandbox_template.json")

        assert config.authorized_imports
        assert config.allowed_directories


PER_CALL_CEILING = 400
"""What one call may spend, against a 1500-token cumulative output budget."""


def test_no_catalogued_model_may_spend_the_whole_run_on_one_call() -> None:
    # MBPP's cumulative output budget is 1500 tokens (DEFAULT_MAX_OUTPUT_TOKENS).
    # A per-call ceiling at that same figure lets the first turn spend the whole
    # run, which is measurably what happened: four turns across a ten-task batch
    # hit 1500 and were cut off before producing any code.
    catalogue = json.loads((REPO_ROOT / "models.json").read_text(encoding="utf-8"))

    ceilings = {
        f"{provider}/{model}": settings["max_tokens"]
        for provider, entry in catalogue["providers"].items()
        for model, settings in entry["models"].items()
    }

    assert ceilings, "the catalogue lists no models"
    too_generous = {
        name: cap for name, cap in ceilings.items() if cap > PER_CALL_CEILING
    }
    assert not too_generous, (
        f"per-call ceiling above {PER_CALL_CEILING}: {too_generous}"
    )
