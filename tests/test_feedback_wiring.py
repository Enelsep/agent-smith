"""The notices reach the model through the paths that really produce them.

`test_feedback` checks the texts. These check that nothing swallows them: a
notice nobody emits is the silent failure the subject is trying to prevent.
"""

from __future__ import annotations

from pathlib import Path

from agent_smith.agent import observation
from agent_smith.extraction import extract_code
from agent_smith.sandbox import feedback
from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import ExecResult, Outcome
from agent_smith.tools.edit_file import edit_file


def test_a_reply_with_no_code_is_reported_as_such() -> None:
    extracted = extract_code("I think the answer is 42.", step=1)
    said = observation.from_extraction(extracted)

    assert said.startswith(feedback.PREFIX)
    assert "```python" in said


def test_a_repaired_block_is_explained_before_its_result() -> None:
    said = observation.from_execution(
        ExecResult(outcome=Outcome.OK, stdout="4\n"),
        repair_note="dropped prose before the code",
    )

    # The explanation leads: it describes the code whose result follows.
    assert said.startswith(feedback.PREFIX)
    assert "dropped prose before the code" in said
    assert said.index("dropped prose") < said.index("4")


def test_a_soft_timeout_keeps_the_partial_output_and_labels_it() -> None:
    said = observation.from_execution(
        ExecResult(
            outcome=Outcome.SOFT_TIMEOUT,
            stdout="row 1\nrow 2\n",
            error="Execution exceeded the sandbox time limit",
        )
    )

    assert "row 1" in said
    assert "incomplete" in said


def test_truncated_output_is_flagged_on_top_of_the_result() -> None:
    said = observation.from_execution(
        ExecResult(outcome=Outcome.OK, stdout="lots\n", truncated=True)
    )

    assert "lots" in said
    assert "narrower" in said


def test_untruncated_output_says_nothing_about_truncation() -> None:
    said = observation.from_execution(ExecResult(outcome=Outcome.OK, stdout="ok\n"))

    assert "narrower" not in said


def test_a_real_timeout_produces_the_partial_output_notice() -> None:
    # End to end through the worker, so the wiring is checked against a real
    # SOFT_TIMEOUT rather than one built by hand.
    with Sandbox(timeout=1.0) as sb:
        result = sb.execute("print('before')\nwhile True:\n    pass\n")

    assert result.outcome is Outcome.SOFT_TIMEOUT
    said = observation.from_execution(result)
    assert "before" in said
    assert "incomplete" in said


def test_a_real_edit_that_breaks_the_file_reports_a_syntax_error(
    tmp_path: Path,
) -> None:
    target = tmp_path / "m.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")

    said = edit_file(str(target), "return 1", "return (1")

    assert "Successfully updated" in said
    assert feedback.PREFIX in said
    assert "syntax error" in said


def test_a_real_edit_that_only_lints_badly_says_lint_not_syntax(
    tmp_path: Path,
) -> None:
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")

    # An unused import parses cleanly and is exactly a lint violation.
    said = edit_file(str(target), "x = 1", "import os\nx = 1")

    assert "Successfully updated" in said
    if feedback.PREFIX in said:  # skipped silently when ruff is unavailable
        assert "lint violation" in said
        assert "syntax error" not in said


def test_a_violation_the_file_already_had_is_not_blamed_on_the_edit(
    tmp_path: Path,
) -> None:
    # Ruff reports the whole file. Telling the model it introduced something
    # that arrived with the file is worse than saying nothing at all.
    target = tmp_path / "m.py"
    target.write_text("import os\nx = 1\n", encoding="utf-8")

    said = edit_file(str(target), "x = 1", "x = 2")

    assert said == f"Successfully updated '{target}'."


def test_a_clean_edit_says_nothing_extra(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")

    said = edit_file(str(target), "return 1", "return 2")

    assert said == f"Successfully updated '{target}'."
