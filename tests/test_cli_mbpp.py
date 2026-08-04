"""The `agent_mbpp` command line entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_smith.cli.mbpp import main as cli
from agent_smith.cli.mbpp.prompt import build_system_prompt, task_prompt
from agent_smith.config import ConfigError
from agent_smith.models.contract import MBPPTaskInput, SolutionOutput

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


def test_the_system_prompt_names_the_delimiter_the_stack_agrees_on() -> None:
    # `<end_code>` is a configured stop sequence and a fence closer in
    # `extraction.strategies`. All three have to name the same token.
    prompt = build_system_prompt(["math"])

    assert "<end_code>" in prompt
    assert "final_answer" in prompt


def test_a_solution_is_written_to_the_requested_path(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "solution.json"

    cli.write_solution(out, cli.failed_run("11", "boom"))

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


def test_the_model_pair_is_optional() -> None:
    # `resolve_config` falls back to the catalogue, so a caller that names
    # neither model nor endpoint still gets a working run.
    args = cli.parse_args(["--task-file", "t.json", "--output", "s.json"])

    assert args.model_name is None
    assert args.provider_url is None


def test_the_task_file_and_output_are_required() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--output", "s.json"])
