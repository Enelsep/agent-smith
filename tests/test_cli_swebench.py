"""The `agent_swebench` command line entry point."""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_smith.cli.swebench import main as cli
from agent_smith.cli.swebench.prompt import build_system_prompt, task_prompt
from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.models.contract import SolutionOutput, SWEBenchTaskInput
from agent_smith.tools.run_tests import PASSED_STATUS

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

    def test_it_names_run_tests_without_quoting_the_script(self) -> None:
        # The harness leaves the evaluation script in the container, so the
        # model calls `run_tests()` with nothing. Quoting the script here cost
        # ~2 000 characters a task against a cumulative ceiling, and every way
        # a retype can fail: a dropped heredoc, a `/bin/bash` guessed in its
        # place, a truncation past the output markers.
        prompt = task_prompt(A_TASK)

        assert "run_tests()" in prompt
        assert "pytest -q sympy/physics/vector/tests/test_vector.py" not in prompt

    def test_hints_are_included_when_the_task_carries_them(self) -> None:
        prompt = task_prompt(A_TASK)

        assert "Look at __add__" in prompt

    def test_a_task_without_hints_does_not_announce_an_empty_section(self) -> None:
        bare = A_TASK.model_copy(update={"hints_text": ""})

        assert "Hints" not in task_prompt(bare)


class TestTheHarnessJudgesTheSubmission:
    def test_a_patch_the_evaluation_script_accepts_goes_through(self) -> None:
        calls: list[tuple[str, dict]] = []

        def tool(name: str, arguments: dict) -> str:
            calls.append((name, arguments))
            return f"{PASSED_STATUS} (Exit code: 0)\nSummary: 3 passed, 0 failed"

        validate = cli.build_validator(A_TASK, tool, "/testbed")

        assert validate("diff --git a/x b/x") is None
        # Judged in the container, on the task's own script, not on whatever
        # the model happened to run last. The script is not passed: the server
        # reads the copy the harness left beside it, which is the same text and
        # cannot be mistyped on the way.
        assert calls == [("run_tests", {"directory": "/testbed"})]

    def test_a_patch_that_fails_is_refused_with_what_failed(self) -> None:
        # Measured: a model submitted the right patch having never seen a test
        # pass. The refusal has to carry the output, or the model is told no
        # and given nothing to act on.
        failure = "Test Run Status: FAILED (Exit code: 1)\nSummary: 0 passed, 2 failed"
        validate = cli.build_validator(
            A_TASK, lambda name, arguments: failure, "/testbed"
        )

        refusal = validate("diff --git a/x b/x")

        assert refusal is not None
        assert failure in refusal
        assert "submit again" in refusal

    def test_a_task_with_no_evaluation_script_accepts_rather_than_refusing(
        self,
    ) -> None:
        # Nothing to judge against. Refusing every submission would be worse
        # than accepting one that was never checked.
        bare = A_TASK.model_copy(update={"eval_script": "   "})
        validate = cli.build_validator(
            bare, lambda name, arguments: "unused", "/testbed"
        )

        assert validate("diff --git a/x b/x") is None

    def test_the_loop_is_given_the_validator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Wiring it is the whole point: a validator the loop never receives
        # lets a run end on a patch nothing checked.
        seen = budget_reaching_the_loop(tmp_path, monkeypatch)

        assert callable(seen["validate_answer"])


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
        # The wall clock is the one the loop does not receive whole: setting up
        # the container spends it too, so what is left is covered by
        # `test_the_setup_time_is_taken_out_of_the_wall_clock`. The ceiling
        # itself is asserted here, with the other three.
        assert cli.MAX_WALL_CLOCK_SECONDS == 900.0

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

    def test_the_setup_time_is_taken_out_of_the_wall_clock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `total_time_seconds` covers the whole command, so pulling the image
        # spends the same 900 seconds the loop would otherwise think it has.
        seen = budget_reaching_the_loop(tmp_path, monkeypatch)

        left = seen["max_wall_clock_seconds"]
        assert isinstance(left, float)
        assert 0.0 < left <= 900.0

    def test_a_setup_longer_than_the_ceiling_leaves_no_budget(self) -> None:
        # A pull that overran must hand the loop zero, not a negative budget the
        # guard would read as unlimited.
        assert cli.remaining_wall_clock(1_000.0) == 0.0
        assert cli.remaining_wall_clock(60.0) == 840.0

    def test_the_package_the_server_imports_travels_with_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `mcp_tools_swebench.py` is a dispatcher over `agent_smith.tools`, and
        # the image has never heard of us. Copying the server alone leaves the
        # container with a file that cannot import its first line.
        budget_reaching_the_loop(tmp_path, monkeypatch)

        copied = {str(destination) for _, destination in COPIES}
        assert cli.SERVER_IN_CONTAINER in copied
        assert cli.PACKAGE_PARENT_IN_CONTAINER in copied

        _, args = CLIENTS[-1]
        assert f"PYTHONPATH={cli.PACKAGE_PARENT_IN_CONTAINER}" in args

    def test_the_repository_path_is_established_not_guessed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every tool defaults to the working directory and the server reads
        # TESTBED_PATH, so a container whose WORKDIR is not the checkout would
        # cost iterations to a model discovering the path by trial.
        budget_reaching_the_loop(tmp_path, monkeypatch)

        _, args = CLIENTS[-1]
        assert args[args.index("-w") + 1] == "/testbed"
        assert "TESTBED_PATH=/testbed" in args

    def test_the_prompt_says_where_the_checkout_is(self) -> None:
        assert "/testbed" in task_prompt(A_TASK, "/testbed")

    def test_the_container_is_cleaned_up_after_a_finished_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A container left running is an explicit failure of the subject, and
        # one leaks per task.
        budget_reaching_the_loop(tmp_path, monkeypatch)

        assert CLEANED == ["cid-123"]

    def test_the_container_is_cleaned_up_when_the_run_blows_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explodes(*_: object, **__: object) -> None:
            raise RuntimeError("the bridge died")

        monkeypatch.setattr(cli, "run_task", explodes)
        solution = solve_with_stubs(tmp_path, monkeypatch)

        assert solution.success is False
        assert "the bridge died" in (solution.error or "")
        assert CLEANED == ["cid-123"]

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

COPIES: list[tuple[Path, str]] = []
"""Every copy the CLI made into the container, as (source, destination)."""

CLEANED: list[str] = []
"""Every container the CLI tore down, by id."""


class _Container:
    """A DockerManager that records instead of talking to Docker."""

    container_id = "cid-123"

    def __init__(self, image: str, name: str | None = None) -> None:
        self.image = image

    def start(self) -> None: ...

    def cleanup(self) -> None:
        CLEANED.append(self.container_id)

    def locate_testbed(self) -> str:
        return "/testbed"

    def copy_in(self, source: Path, destination: str) -> None:
        COPIES.append((source, destination))


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
    COPIES.clear()
    CLEANED.clear()

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

    monkeypatch.setattr(cli, "run_task", spy)
    solve_with_stubs(tmp_path, monkeypatch)
    return seen


def solve_with_stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SolutionOutput:
    """`solve` with Docker, the bridge and the provider all faked.

    The loop itself is left to the caller: `budget_reaching_the_loop` replaces
    it with a spy, a test about failure replaces it with a raise.
    """
    monkeypatch.setattr(cli, "DockerManager", _Container)
    _stub_the_rest(monkeypatch)

    return cli.solve(
        cli.parse_args(
            [
                "--task-file",
                str(written_task(tmp_path)),
                "--output",
                str(tmp_path / "s.json"),
            ]
        )
    )
