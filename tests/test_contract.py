"""Contract tests — lock the frozen Pydantic models the moulinette validates against.

`contract.py` is a verbatim copy of `moulinette/models_public.py` and must not drift.
These tests fail loudly if a field is renamed, removed or retyped, and pin the two traps
the moulinette is strict about:
  - `SolutionOutput.task_id` is a *string* (MBPP `task_id` is an int and must be stringified);
  - an int `task_id` is rejected before any correctness check even runs.
"""

import json

import pytest
from pydantic import ValidationError

from agent_smith.models.contract import (
    MBPPTaskInput,
    SandboxConfig,
    SolutionOutput,
    StepMetrics,
    SWEBenchTaskInput,
)


def _sample_solution() -> dict:
    """A minimal-but-complete solution.json payload, as our agent would emit it."""
    return {
        "task_id": "282",
        "benchmark": "mbpp",
        "success": True,
        "solution": "def add(a, b):\n    return a + b",
        "iterations": 2,
        "total_requests": 3,
        "total_input_tokens": 1200,
        "total_output_tokens": 340,
        "total_time_seconds": 4.2,
        "steps": [
            {
                "step": 1,
                "input_tokens": 800,
                "output_tokens": 120,
                "request_time_ms": 950.0,
                "api_url": "https://api.groq.com/openai/v1",
                "model_name": "llama-3.3-70b-versatile",
                "llm_output": "Thought: ...\n```python\nresult = run_tests(...)\n```",
                "sandbox_input": "result = run_tests(code=...)",
                "sandbox_output": "1 passed",
                "retries": 0,
            },
            {
                "step": 2,
                "input_tokens": 400,
                "output_tokens": 220,
                "request_time_ms": 700.0,
                "llm_output": "final_answer(...)",
                "sandbox_input": "final_answer(...)",
                "sandbox_output": "",
                "retries": 1,
            },
        ],
        "system_prompt": "You are an autonomous coding agent...",
    }


# --- round trip -------------------------------------------------------------


def test_solution_output_roundtrips_through_json() -> None:
    data = _sample_solution()
    # exactly what the moulinette does before scoring:
    obj = SolutionOutput.model_validate(data)
    # serialise and re-validate; the shape must survive a full JSON round trip
    reloaded = SolutionOutput.model_validate(json.loads(obj.model_dump_json()))
    assert reloaded.task_id == "282"
    assert reloaded.benchmark == "mbpp"
    assert reloaded.iterations == 2
    assert len(reloaded.steps) == 2
    assert reloaded.steps[0].sandbox_output == "1 passed"
    assert reloaded.system_prompt.startswith("You are")


def test_solution_output_serialises_task_id_as_json_string() -> None:
    obj = SolutionOutput.model_validate(_sample_solution())
    dumped = json.loads(obj.model_dump_json())
    assert isinstance(dumped["task_id"], str)


# --- the task_id trap -------------------------------------------------------


def test_task_id_is_string_when_built_from_mbpp_int() -> None:
    mbpp = MBPPTaskInput(
        task_id=282,
        task_definition="Add two numbers",
        function_definition="def add(a, b):",
    )
    assert isinstance(mbpp.task_id, int)
    # the builder must stringify it; a str value is accepted and preserved
    payload = {**_sample_solution(), "task_id": str(mbpp.task_id)}
    assert SolutionOutput.model_validate(payload).task_id == "282"


def test_int_task_id_is_rejected() -> None:
    bad = {**_sample_solution(), "task_id": 282}  # int, not str
    with pytest.raises(ValidationError):
        SolutionOutput.model_validate(bad)


# --- required fields --------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        "task_id",
        "benchmark",
        "success",
        "solution",
        "iterations",
        "total_requests",
        "total_input_tokens",
        "total_output_tokens",
        "total_time_seconds",
    ],
)
def test_required_solution_fields_are_enforced(missing: str) -> None:
    data = _sample_solution()
    del data[missing]
    with pytest.raises(ValidationError):
        SolutionOutput.model_validate(data)


# --- contract shape (guards against verbatim drift) -------------------------


def test_solution_output_has_all_contract_fields() -> None:
    expected = {
        "task_id",
        "benchmark",
        "success",
        "solution",
        "iterations",
        "total_requests",
        "total_input_tokens",
        "total_output_tokens",
        "total_time_seconds",
        "steps",
        "system_prompt",
        "error",
        "timestamp",
    }
    assert expected <= set(SolutionOutput.model_fields)


def test_step_metrics_has_all_contract_fields() -> None:
    expected = {
        "step",
        "input_tokens",
        "output_tokens",
        "request_time_ms",
        "timestamp",
        "api_url",
        "model_name",
        "llm_output",
        "sandbox_input",
        "sandbox_output",
        "retries",
    }
    assert expected <= set(StepMetrics.model_fields)


# --- defaults ---------------------------------------------------------------


def test_step_metrics_optional_defaults() -> None:
    step = StepMetrics(step=1, input_tokens=10, output_tokens=5, request_time_ms=1.0)
    assert step.retries == 0
    assert step.api_url == "" and step.model_name == ""
    assert (
        step.llm_output == "" and step.sandbox_input == "" and step.sandbox_output == ""
    )
    assert step.timestamp  # auto ISO timestamp populated


def test_solution_output_defaults() -> None:
    obj = SolutionOutput.model_validate(
        {
            k: v
            for k, v in _sample_solution().items()
            if k not in ("steps", "system_prompt")
        }
    )
    assert obj.steps == []
    assert obj.system_prompt == ""
    assert obj.error is None


def test_sandbox_config_defaults() -> None:
    cfg = SandboxConfig()
    assert cfg.max_execution_time_seconds == 30
    assert cfg.max_memory_mb == 512
    assert cfg.authorized_imports == [] and cfg.allowed_directories == []


# --- SWE-bench input smoke --------------------------------------------------


def test_swebench_task_input_optional_fields() -> None:
    task = SWEBenchTaskInput(
        instance_id="sympy__sympy-14711",
        problem_statement="...",
        docker_image="swebench/sweb.eval.x86_64.sympy_1776_sympy-14711:latest",
        eval_script="pytest -q",
    )
    assert task.hints_text == "" and task.repo == ""
