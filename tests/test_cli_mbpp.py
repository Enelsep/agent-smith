"""The `agent_mbpp` command line entry point."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_smith.cli.mbpp import main as cli
from agent_smith.cli.mbpp import prompt as prompt_module
from agent_smith.cli.mbpp.prompt import build_system_prompt, task_prompt
from agent_smith.config import ConfigError
from agent_smith.models.contract import MBPPTaskInput, SolutionOutput
from agent_smith.sandbox.protocol import ExecResult, Outcome

A_TASK = MBPPTaskInput(
    task_id=11,
    task_definition="Write a function to remove the first and last occurrence of a character.",
    function_definition="def remove_Occ(s, ch):",
    test_imports=["import math"],
    test_list=['assert remove_Occ("hello", "l") == "heo"'],
)


def written_task(tmp_path: Path, task: MBPPTaskInput = A_TASK) -> Path:
    """A task file in the shape the contract serialises."""
    path = tmp_path / "task.json"
    path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_it_reads_a_task_file_back_into_the_contract(tmp_path: Path) -> None:
    assert cli.load_task(written_task(tmp_path)) == A_TASK


def test_an_absent_task_file_is_explained_not_raised_raw(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        cli.load_task(tmp_path / "nothing-here.json")


def test_a_malformed_task_file_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    path.write_text('{"task_id": "not an int"}', encoding="utf-8")

    with pytest.raises(ConfigError, match="task.json"):
        cli.load_task(path)


def test_the_spec_stringifies_the_id_the_contract_types_as_int() -> None:
    spec = cli.build_task_spec(A_TASK, "sys")

    assert spec.task_id == "11"
    assert spec.benchmark == "mbpp"
    assert spec.system_prompt == "sys"


def test_the_task_prompt_carries_what_the_model_needs_to_solve() -> None:
    prompt = task_prompt(A_TASK)

    assert "remove the first and last occurrence" in prompt
    assert "def remove_Occ(s, ch):" in prompt
    assert 'assert remove_Occ("hello", "l") == "heo"' in prompt
    assert "import math" in prompt


def test_the_task_prompt_says_the_visible_tests_are_a_subset() -> None:
    # A solution is judged on the full test set, not the public assertions the
    # task file carries. A model that fits only what it can see fails the rest.
    assert "hidden" in task_prompt(A_TASK).lower()


def test_a_task_without_visible_tests_still_produces_a_prompt() -> None:
    bare = MBPPTaskInput(
        task_id=1,
        task_definition="Add two numbers.",
        function_definition="def add(a, b):",
    )

    prompt = task_prompt(bare)

    assert "Add two numbers." in prompt
    assert "def add(a, b):" in prompt


def test_the_system_prompt_quotes_the_sandbox_allowlist() -> None:
    # Hardcoding the list would let it drift from the sandbox configuration, and
    # the model would spend an iteration discovering a refused import.
    prompt = build_system_prompt(["math", "collections.*"])

    assert "math" in prompt
    assert "collections.*" in prompt


def test_the_prompt_file_may_carry_braces_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The point of holding the prompt in a file is that its wording can be
    # revised without touching Python. An example carrying a dict literal is
    # ordinary prompt material, so it must reach the model as written rather
    # than being read as a field to substitute.
    monkeypatch.setattr(
        prompt_module,
        "load_prompt",
        lambda name: "Only these: {imports}. Example: counts = {'a': 1}",
    )

    built = build_system_prompt(["math"])

    assert built == "Only these: math. Example: counts = {'a': 1}"


def test_the_system_prompt_names_the_delimiter_the_stack_agrees_on() -> None:
    # `<end_code>` is a configured stop sequence and a fence closer in
    # `extraction.strategies`. All three have to name the same token.
    prompt = build_system_prompt(["math"])

    assert "<end_code>" in prompt
    assert "final_answer" in prompt


def test_a_solution_is_written_to_the_requested_path(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "solution.json"

    cli.write_solution(out, cli._failed("11", "boom"))

    written = SolutionOutput.model_validate_json(out.read_text(encoding="utf-8"))
    assert written.task_id == "11"
    assert written.success is False
    assert written.error == "boom"


def test_a_run_that_cannot_start_still_leaves_a_valid_solution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A crash scores as an automatic fail, so the unhappy path must still
    # produce a readable solution file.
    out = tmp_path / "solution.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent_mbpp",
            "--task-file",
            str(tmp_path / "absent.json"),
            "--output",
            str(out),
        ],
    )

    cli.main()

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["success"] is False
    assert written["benchmark"] == "mbpp"
    assert "cannot read" in written["error"]


def test_it_returns_rather_than_exits_when_it_wrote_a_solution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unsolved task and a crashed program are different outcomes; only the
    # solution file distinguishes them, so a written failure exits cleanly.
    out = tmp_path / "solution.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent_mbpp",
            "--task-file",
            str(tmp_path / "absent.json"),
            "--output",
            str(out),
        ],
    )

    cli.main()  # must return, not raise SystemExit


def budget_reaching_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    """Run `solve` against a stubbed loop and return the budget it was handed."""
    seen: dict[str, object] = {}

    def spy(task, provider, sandbox, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return cli._failed(task.task_id, "stopped after recording the budget")

    monkeypatch.setattr(cli, "run_task", spy)
    monkeypatch.setattr(cli, "build_provider", lambda config: object())
    monkeypatch.setattr(
        cli,
        "resolve_config",
        lambda **_: SimpleNamespace(
            base_url="https://example.invalid/v1",
            model_name="a-model",
            stop=[],
            max_tokens=1500,
            api_keys=["k"],
            sandbox=SimpleNamespace(
                max_execution_time_seconds=5.0, authorized_imports=["math"]
            ),
        ),
    )

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


def test_the_configured_per_call_ceiling_reaches_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cumulative ceilings say nothing about one request. Without
    # `max_tokens_per_call` the loop offers the whole remaining output budget to
    # every request, which silently overrides the `max_tokens` models.json
    # configures.
    seen = budget_reaching_the_loop(tmp_path, monkeypatch)

    assert seen["max_tokens_per_call"] == 1500


def test_the_exam_ceilings_are_what_the_loop_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The four limits of the subject, VI.1.1. Written as literals rather than
    # read from the loop's defaults: the point of this card is that the CLI
    # runs at these numbers, so a change to a default elsewhere must fail here
    # rather than be adopted silently.
    seen = budget_reaching_the_loop(tmp_path, monkeypatch)

    assert seen["max_iterations"] == 10
    assert seen["max_input_tokens"] == 6000
    assert seen["max_output_tokens"] == 1500
    assert seen["max_wall_clock_seconds"] == 120.0


def test_the_model_pair_is_optional() -> None:
    # `resolve_config` falls back to the catalogue, so a caller that names
    # neither model nor endpoint still gets a working run.
    args = cli.parse_args(["--task-file", "t.json", "--output", "s.json"])

    assert args.model_name is None
    assert args.provider_url is None


def test_the_task_file_and_output_are_required() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--output", "s.json"])


class TestTheSubmissionIsChecked:
    """`build_validator` runs the task's own assertions against a submission."""

    def a_sandbox(self, script: list[object]) -> object:
        class Sandbox:
            def __init__(self) -> None:
                self.ran: list[str] = []
                self.restarts = 0

            def execute(self, code: str):  # type: ignore[no-untyped-def]
                self.ran.append(code)
                return script.pop(0)

        return Sandbox()

    def test_a_submission_that_passes_the_given_tests_is_accepted(self) -> None:
        sandbox = self.a_sandbox([ExecResult(outcome=Outcome.OK, stdout="")])

        validate = cli.build_validator(A_TASK, sandbox)  # type: ignore[arg-type]

        assert validate("def remove_Occ(s, ch): return s") is None

    def test_the_submitted_source_runs_with_the_task_s_own_assertions(self) -> None:
        # Not the code the model happened to run: the string it submitted, which
        # is what the grader will run, and the assertions exactly as given.
        sandbox = self.a_sandbox([ExecResult(outcome=Outcome.OK, stdout="")])

        cli.build_validator(A_TASK, sandbox)("def remove_Occ(s, ch): return s")  # type: ignore[arg-type]

        ran = sandbox.ran[0]  # type: ignore[attr-defined]
        assert "def remove_Occ(s, ch): return s" in ran
        assert 'assert remove_Occ("hello", "l") == "heo"' in ran
        assert "import math" in ran

    def test_a_failing_assertion_is_refused_and_quoted_back(self) -> None:
        sandbox = self.a_sandbox(
            [
                ExecResult(
                    outcome=Outcome.ERROR,
                    stderr="AssertionError",
                    error="AssertionError",
                )
            ]
        )

        refusal = cli.build_validator(A_TASK, sandbox)(
            "def remove_Occ(s, ch): return s"
        )  # type: ignore[arg-type]

        assert refusal is not None
        assert "AssertionError" in refusal

    def test_a_task_with_no_visible_tests_accepts_whatever_it_is_given(self) -> None:
        # Nothing to check against, so there is nothing to refuse on -- and
        # running the source alone would reject a perfectly good answer for
        # printing nothing.
        bare = MBPPTaskInput(
            task_id=1, task_definition="Add.", function_definition="def add(a, b):"
        )
        sandbox = self.a_sandbox([])

        assert cli.build_validator(bare, sandbox)("def add(a, b): return a + b") is None  # type: ignore[arg-type]
        assert sandbox.ran == []  # type: ignore[attr-defined]
