"""The contract we emit is the contract the moulinette validates.

`contract.py` is not a byte-for-byte copy of `moulinette/models_public.py`: the
`Field(...)` calls are wrapped over several lines and the annotations use modern
syntax (`list[str]` rather than `List[str]`, `str | None` rather than
`Optional[str]`). None of that changes the JSON schema, and the schema is the
level the moulinette actually reads us at — so that is the level this guard
compares. It catches what matters, a renamed field, a changed type or a lost
default, and stays immune to the two things that have already changed harmlessly.

`moulinette/` is gitignored and exists on no machine but a maintainer's, so the
comparison skips rather than fails when it is absent.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from pydantic import BaseModel, Field

from agent_smith.models import contract

REFERENCE = Path(__file__).resolve().parent.parent / "moulinette" / "models_public.py"

# The five models the moulinette validates. Named explicitly rather than
# discovered, so deleting one of ours makes this list fail rather than shrink.
CONTRACT_MODELS = (
    "MBPPTaskInput",
    "SWEBenchTaskInput",
    "SandboxConfig",
    "SolutionOutput",
    "StepMetrics",
)

needs_reference = pytest.mark.skipif(
    not REFERENCE.exists(),
    reason=f"{REFERENCE.name} is gitignored and absent from this checkout",
)


def _load_reference() -> ModuleType:
    """Import the moulinette's models from a path outside the package tree."""
    spec = importlib.util.spec_from_file_location("models_public", REFERENCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model(module: ModuleType, name: str) -> type[BaseModel]:
    return cast(type[BaseModel], getattr(module, name))


def schema_differences(ours: type[BaseModel], theirs: type[BaseModel]) -> list[str]:
    """Every way the two models would present differently in JSON.

    Compares the generated schemas field by field rather than as whole
    documents, so a failure names the field that drifted instead of printing
    two schemas side by side. The top-level title is left out: it is the class
    name, and the caller already knows which two it paired up.
    """
    ours_schema = ours.model_json_schema()
    theirs_schema = theirs.model_json_schema()
    ours_properties = ours_schema.get("properties", {})
    theirs_properties = theirs_schema.get("properties", {})

    differences: list[str] = []
    for name in sorted(set(ours_properties) | set(theirs_properties)):
        if name not in theirs_properties:
            differences.append(f"{name}: ours only")
        elif name not in ours_properties:
            differences.append(f"{name}: theirs only")
        elif ours_properties[name] != theirs_properties[name]:
            differences.append(
                f"{name}: {ours_properties[name]} != {theirs_properties[name]}"
            )

    ours_required = sorted(ours_schema.get("required", []))
    theirs_required = sorted(theirs_schema.get("required", []))
    if ours_required != theirs_required:
        differences.append(f"required: {ours_required} != {theirs_required}")

    return differences


class TestSchemaDifferences:
    """The detector itself, on models whose drift we control."""

    def test_the_same_shape_under_two_names_differs_in_nothing(self) -> None:
        class Ours(BaseModel):
            task_id: str
            iterations: int

        class Theirs(BaseModel):
            task_id: str
            iterations: int

        assert schema_differences(Ours, Theirs) == []

    def test_a_renamed_field_is_reported_under_both_names(self) -> None:
        class Ours(BaseModel):
            task_id: str

        class Theirs(BaseModel):
            taskId: str

        # A rename moves the required list too, so it is reported three ways.
        assert sorted(schema_differences(Ours, Theirs)) == [
            "required: ['task_id'] != ['taskId']",
            "taskId: theirs only",
            "task_id: ours only",
        ]

    def test_a_retyped_field_is_reported(self) -> None:
        class Ours(BaseModel):
            task_id: int

        class Theirs(BaseModel):
            task_id: str

        differences = schema_differences(Ours, Theirs)
        assert len(differences) == 1
        assert differences[0].startswith("task_id:")

    def test_a_lost_default_is_reported(self) -> None:
        class Ours(BaseModel):
            error: str

        class Theirs(BaseModel):
            error: str = Field(default="")

        differences = schema_differences(Ours, Theirs)
        assert any(difference.startswith("error:") for difference in differences)
        assert any(difference.startswith("required:") for difference in differences)

    def test_a_changed_description_is_reported(self) -> None:
        # Descriptions travel into the schema the moulinette reads, so a
        # reworded one is drift like any other.
        class Ours(BaseModel):
            benchmark: str = Field(..., description="Benchmark type")

        class Theirs(BaseModel):
            benchmark: str = Field(..., description="Which benchmark this is")

        assert len(schema_differences(Ours, Theirs)) == 1


@needs_reference
class TestContractEquivalence:
    """Our models against the ones the moulinette will validate us with."""

    @pytest.mark.parametrize("name", CONTRACT_MODELS)
    def test_our_model_presents_the_reference_schema(self, name: str) -> None:
        reference = _load_reference()
        assert schema_differences(_model(contract, name), _model(reference, name)) == []

    def test_the_reference_really_is_a_second_module(self) -> None:
        # Guards the test above against passing because the loader handed back
        # our own module and compared it with itself.
        reference = _load_reference()
        assert reference.__file__ != contract.__file__
        differences = schema_differences(
            _model(contract, "StepMetrics"), _model(reference, "SolutionOutput")
        )
        assert differences != []
