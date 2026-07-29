import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent_smith.models.contract import MBPPTaskInput, SWEBenchTaskInput

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _assert_strict_fixture_validation(
    item: dict[str, Any], model_cls: type[BaseModel]
) -> None:
    """
    Validates that a fixture dictionary strictly matches a Pydantic model
    contract:
    1. Rejects any unexpected or misspelled keys (extra fields).
    2. Validates types and required fields via model_validate.
    3. Guarantees no data loss between JSON inputs and model fields.
    """
    allowed_fields = set(model_cls.model_fields.keys())
    item_keys = set(item.keys())
    # 1. Catch typos in JSON key names (extra keys not present
    # in Pydantic schema)
    extra_keys = item_keys - allowed_fields
    assert not extra_keys, (
        f"Fixture contains unexpected or misspelled keys: {extra_keys}. "
        f"Allowed fields in {model_cls.__name__} are: {allowed_fields}"
    )
    # 2. Validate types and required fields
    model_instance = model_cls.model_validate(item)
    # 3. Ensure no data loss (compare model output back with
    # original json item)
    dumped = model_instance.model_dump(mode="python")
    for key, expected_value in item.items():
        assert dumped[key] == expected_value, (
            f"Data mismatch for key '{key}': expected {expected_value!r}, "
            f"got {dumped[key]!r}"
        )


def test_mbpp_fixtures_comply_with_contract() -> None:
    """Ensure MBPP fixture tasks strictly match the MBPPTaskInput contract
    without extra keys or lost data."""
    fixture_path = FIXTURES_DIR / "mbpp_tasks.json"
    assert fixture_path.exists(), "MBPP fixture file is missing"

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) > 0, "MBPP fixture file is empty"
    for item in data:
        _assert_strict_fixture_validation(item, MBPPTaskInput)


def test_swebench_fixtures_comply_with_contract() -> None:
    """Ensure SWE-bench fixture tasks strictly match the SWEBenchTaskInput
    contract without extra keys or lost data."""
    fixture_path = FIXTURES_DIR / "swebench_tasks.json"
    assert fixture_path.exists(), "SWE-bench fixture file is missing"

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) > 0, "SWE-bench fixture file is empty"
    for item in data:
        _assert_strict_fixture_validation(item, SWEBenchTaskInput)
