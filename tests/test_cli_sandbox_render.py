"""What the prompt prints for each way an execution can end."""

from __future__ import annotations

from agent_smith.cli.sandbox.render import MARKER, render
from agent_smith.sandbox.protocol import ExecResult, Outcome


def test_output_is_printed_exactly_as_the_code_wrote_it() -> None:
    # No prefix, no quoting: a person reading their own program's output
    # should see their own program's output.
    rendered = render(ExecResult(outcome=Outcome.OK, stdout="hello\nworld\n"))

    assert rendered == "hello\nworld"


def test_an_entry_that_printed_nothing_renders_empty() -> None:
    assert render(ExecResult(outcome=Outcome.OK)) == ""


def test_stderr_is_shown_even_when_nothing_reached_stdout() -> None:
    assert "warned" in render(ExecResult(outcome=Outcome.OK, stderr="warned\n"))


def test_both_streams_are_shown_together() -> None:
    rendered = render(
        ExecResult(outcome=Outcome.OK, stdout="out", stderr="err"),
    )

    assert rendered == "out\nerr"


def test_a_traceback_is_shown_unprefixed() -> None:
    # It is already addressed to whoever wrote the code, and a marker would
    # only push the interesting last line further right.
    traceback = 'Traceback:\n  File "<sandbox>"\nZeroDivisionError: division by zero'
    rendered = render(ExecResult(outcome=Outcome.ERROR, error=traceback))

    assert rendered == traceback


def test_partial_output_survives_the_error_that_followed_it() -> None:
    rendered = render(
        ExecResult(outcome=Outcome.ERROR, stdout="got this far\n", error="boom"),
    )

    assert rendered == "got this far\nboom"


def test_a_refusal_names_the_outcome_and_carries_the_marker() -> None:
    # A blocked import and a raised ImportError look alike otherwise, and only
    # one of them means "the sandbox stopped you".
    rendered = render(
        ExecResult(outcome=Outcome.BLOCKED, error="import of 'socket' is not allowed"),
    )

    assert rendered.startswith(MARKER)
    assert "blocked" in rendered
    assert "socket" in rendered


def test_a_soft_timeout_keeps_the_output_that_arrived_first() -> None:
    rendered = render(
        ExecResult(
            outcome=Outcome.SOFT_TIMEOUT,
            stdout="printed before the deadline\n",
            error="Execution exceeded the sandbox time limit",
        ),
    )

    assert "printed before the deadline" in rendered
    assert "soft_timeout" in rendered


def test_a_final_answer_is_labelled_and_shown_whole() -> None:
    answer = "def solve(n):\n    return n * 2"
    rendered = render(
        ExecResult(outcome=Outcome.FINAL_ANSWER, final_answer=answer),
    )

    assert MARKER in rendered
    assert answer in rendered


def test_final_answer_without_a_value_says_so() -> None:
    rendered = render(ExecResult(outcome=Outcome.FINAL_ANSWER))

    assert "without a value" in rendered


def test_truncation_is_announced_rather_than_left_to_be_noticed() -> None:
    rendered = render(
        ExecResult(outcome=Outcome.OK, stdout="a lot of output", truncated=True),
    )

    assert "truncated" in rendered


def test_a_restarted_worker_warns_that_the_namespace_is_gone() -> None:
    rendered = render(ExecResult(outcome=Outcome.OK, namespace_reset=True))

    assert "restarted" in rendered


def test_an_outcome_with_no_detail_still_renders_something_readable() -> None:
    rendered = render(ExecResult(outcome=Outcome.CRASHED))

    assert "crashed" in rendered
    assert rendered.startswith(MARKER)
