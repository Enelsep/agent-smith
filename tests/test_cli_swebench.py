"""The `agent_swebench` command line entry point."""

from __future__ import annotations

from agent_smith.cli.swebench.prompt import build_system_prompt, task_prompt
from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.models.contract import SWEBenchTaskInput

A_TASK = SWEBenchTaskInput(
    instance_id="sympy__sympy-14711",
    problem_statement="vector add 0 error: adding a zero vector raises TypeError.",
    docker_image="swebench/sweb.eval.x86_64.sympy_1776_sympy-14711:latest",
    eval_script="#!/bin/bash\npytest -q sympy/physics/vector/tests/test_vector.py",
    hints_text="Look at __add__ in sympy/physics/vector.",
    repo="sympy/sympy",
)

TOOLS = [
    MCPToolDefinition(
        name="read_file",
        description="Read a file, optionally a line range.",
        input_schema={
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "start_line": {"type": "integer"},
            },
            "required": ["filepath"],
        },
    ),
]


class TestTheSystemPromptDocumentsTheServerItIsGiven:
    def test_the_tool_list_comes_from_the_connected_server(self) -> None:
        # Written by hand the list drifts from whatever server is actually
        # connected, and the model is told about tools that do not exist — or
        # not told about the ones that do. SBX-8 generates it from the schemas
        # the server publishes, which is what makes an unknown server usable.
        prompt = build_system_prompt(TOOLS)

        assert "read_file(filepath: str, start_line: int = None)" in prompt

    def test_the_turn_contract_survives_the_generated_section(self) -> None:
        # The parts that are not about tools have to come through untouched:
        # `<end_code>` is a configured stop sequence, and the submission call
        # is the only thing that ends the task.
        prompt = build_system_prompt(TOOLS)

        assert "<end_code>" in prompt
        assert "final_answer(get_patch())" in prompt
        assert "Thought:" not in prompt


class TestTheTaskPrompt:
    def test_it_carries_what_the_agent_needs_to_start(self) -> None:
        prompt = task_prompt(A_TASK)

        assert "adding a zero vector raises TypeError" in prompt
        assert "sympy/sympy" in prompt

    def test_it_carries_the_eval_script_the_run_tests_tool_expects(self) -> None:
        # `run_tests(eval_script, directory)` takes the script as an argument,
        # so the model cannot call it without having been given the text.
        prompt = task_prompt(A_TASK)

        assert "pytest -q sympy/physics/vector/tests/test_vector.py" in prompt

    def test_hints_are_included_when_the_task_carries_them(self) -> None:
        prompt = task_prompt(A_TASK)

        assert "Look at __add__" in prompt

    def test_a_task_without_hints_does_not_announce_an_empty_section(self) -> None:
        bare = A_TASK.model_copy(update={"hints_text": ""})

        assert "Hints" not in task_prompt(bare)
