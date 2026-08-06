"""The `agent_swebench` command line entry point."""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_smith.cli.swebench import main as cli
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


class TestTheRun:
    def test_the_swebench_ceilings_are_what_the_loop_is_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The four limits of the subject, VI.1.2. `run_task` defaults to MBPP's,
        # which are narrower on every axis, so a CLI that forgets to pass these
        # silently runs a SWE-bench task under an MBPP budget.
        seen = budget_reaching_the_loop(tmp_path, monkeypatch)

        assert seen["max_iterations"] == 30
        assert seen["max_input_tokens"] == 300_000
        assert seen["max_output_tokens"] == 10_000
        assert seen["max_wall_clock_seconds"] == 900.0

    def test_the_tools_are_reached_inside_the_container(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The repository only exists inside the image, so a server started on
        # this machine would read the wrong files -- or none.
        budget_reaching_the_loop(tmp_path, monkeypatch)

        assert CLIENTS, "no MCP client was built"
        command, args = CLIENTS[-1]
        assert command == "docker"
        assert args[:2] == ["exec", "-i"]
        assert "cid-123" in args

    def test_an_unreadable_task_file_still_leaves_a_valid_solution(
        self, tmp_path: Path
    ) -> None:
        solution = cli.solve(
            cli.parse_args(
                [
                    "--task-file",
                    str(tmp_path / "absent.json"),
                    "--output",
                    str(tmp_path / "s.json"),
                ]
            )
        )

        assert solution.success is False
        assert solution.benchmark == "swebench"
        assert "cannot read" in (solution.error or "")

    def test_a_container_that_will_not_start_is_reported_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Docker is the one dependency guaranteed to fail on someone's machine.
        def refuses(self: object) -> None:
            raise RuntimeError("no such image")

        monkeypatch.setattr(cli.DockerManager, "start", refuses)
        _stub_the_rest(monkeypatch)

        solution = cli.solve(
            cli.parse_args(
                [
                    "--task-file",
                    str(written_task(tmp_path)),
                    "--output",
                    str(tmp_path / "s.json"),
                ]
            )
        )

        assert solution.success is False
        assert solution.task_id == "sympy__sympy-14711"
        assert "no such image" in (solution.error or "")


CLIENTS: list[tuple[str, list[str]]] = []
"""Every MCP client the CLI built, as (command, args)."""


class _Container:
    """A DockerManager that records instead of talking to Docker."""

    container_id = "cid-123"

    def __init__(self, image: str, name: str | None = None) -> None:
        self.image = image

    def start(self) -> None: ...
    def cleanup(self) -> None: ...
    def copy_in(self, source: Path, destination: str) -> None: ...


class _Bridge:
    """An MCPBridge over a server that offers the one tool the tests use."""

    def __init__(self, client: object) -> None:
        self.tool_defs = TOOLS

    def start(self) -> None: ...
    def close(self) -> None: ...
    def call(self, name: str, arguments: dict[str, object]) -> str:
        return ""


def _stub_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything the CLI reaches for that is not the subject of the test."""
    CLIENTS.clear()

    def record(command: str, args: list[str]) -> object:
        CLIENTS.append((command, args))
        return object()

    monkeypatch.setattr(cli, "UnifiedMCPClient", record)
    monkeypatch.setattr(cli, "MCPBridge", _Bridge)
    monkeypatch.setattr(cli, "build_provider", lambda config: object())
    monkeypatch.setattr(
        cli.Sandbox,
        "from_config",
        classmethod(lambda cls, config, **kw: contextlib.nullcontext(object())),
    )
    monkeypatch.setattr(
        cli,
        "resolve_config",
        lambda **_: SimpleNamespace(max_tokens=1500, sandbox=object()),
    )


def written_task(tmp_path: Path, task: SWEBenchTaskInput = A_TASK) -> Path:
    path = tmp_path / "task.json"
    path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    return path


def budget_reaching_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    """Run `solve` against a stubbed loop and return the budget it was handed."""
    seen: dict[str, object] = {}

    def spy(task, provider, sandbox, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return cli._failed(task.task_id, "stopped after recording the budget")

    monkeypatch.setattr(cli, "DockerManager", _Container)
    monkeypatch.setattr(cli, "run_task", spy)
    _stub_the_rest(monkeypatch)

    cli.solve(
        cli.parse_args(
            [
                "--task-file",
                str(written_task(tmp_path)),
                "--output",
                str(tmp_path / "s.json"),
            ]
        )
    )
    return seen
