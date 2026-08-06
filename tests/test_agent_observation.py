"""Every way a step can end, rendered as the text the model reads next."""

import pytest

from agent_smith.agent import observation
from agent_smith.extraction import ExtractionResult
from agent_smith.sandbox import feedback
from agent_smith.sandbox.protocol import ExecResult, Outcome


def test_a_reply_with_no_code_reports_what_extraction_said() -> None:
    # The extractor's own account is kept -- it is the part that tells the
    # model what to change -- and wrapped in the sandbox's standard notice.
    result = ExtractionResult(failure="Could not read your reply: no code block.")

    said = observation.from_extraction(result)

    assert "Could not read your reply: no code block" in said
    assert said.startswith(feedback.PREFIX)


def test_output_that_printed_something_is_shown_as_is() -> None:
    executed = ExecResult(outcome=Outcome.OK, stdout="42\n")

    assert observation.from_execution(executed) == "42"


def test_silent_success_says_so_rather_than_showing_nothing() -> None:
    executed = ExecResult(outcome=Outcome.OK)

    assert observation.from_execution(executed) == observation.NO_OUTPUT


def test_stderr_is_shown_alongside_stdout() -> None:
    # The worker captures the two streams separately, and code that writes to
    # stderr deliberately would otherwise be invisible to the model.
    executed = ExecResult(outcome=Outcome.OK, stdout="out\n", stderr="warned\n")

    assert observation.from_execution(executed) == "out\nwarned"


def test_a_traceback_reaches_the_model() -> None:
    executed = ExecResult(
        outcome=Outcome.ERROR, error="Traceback...\nZeroDivisionError: division by zero"
    )

    assert "ZeroDivisionError" in observation.from_execution(executed)


def test_a_soft_timeout_keeps_the_partial_output_and_names_the_cause() -> None:
    executed = ExecResult(
        outcome=Outcome.SOFT_TIMEOUT,
        stdout="partial\n",
        error="Execution exceeded the sandbox time limit",
    )

    said = observation.from_execution(executed)

    assert "partial" in said
    # What the model needs is not that a limit exists but that the output
    # above stops mid-run; `feedback.partial_output` says so.
    assert "time limit" in said
    assert "incomplete" in said


@pytest.mark.parametrize(
    "outcome", [Outcome.HARD_TIMEOUT, Outcome.CRASHED, Outcome.SHUTDOWN]
)
def test_an_outcome_with_no_message_still_names_itself(outcome: Outcome) -> None:
    # HARD_TIMEOUT and CRASHED are built by the parent and normally carry a
    # message. An empty one must still produce a usable observation.
    said = observation.from_execution(ExecResult(outcome=outcome))

    assert outcome.value in said


def test_a_lost_namespace_is_announced() -> None:
    executed = ExecResult(outcome=Outcome.OK, stdout="fine\n")

    said = observation.from_execution(executed, namespace_lost=True)

    assert "fine" in said
    assert said.endswith(observation.NAMESPACE_LOST)


def test_a_repair_is_reported_so_the_model_stops_repeating_it() -> None:
    executed = ExecResult(outcome=Outcome.OK, stdout="fine\n")

    said = observation.from_execution(
        executed, repair_note="closed an unterminated string or bracket"
    )

    # The explanation leads, because it describes the code whose result follows.
    assert said.startswith(feedback.PREFIX)
    assert "closed an unterminated string or bracket" in said
    assert "fine" in said


def test_final_answer_called_with_nothing_asks_for_the_answer_again() -> None:
    executed = ExecResult(outcome=Outcome.FINAL_ANSWER, final_answer=None)

    assert observation.from_execution(executed) == observation.EMPTY_ANSWER


def test_a_final_answer_that_carries_a_value_is_shown_rather_than_denied() -> None:
    # The loop consumes a valued answer instead of asking for an observation,
    # so nothing in the current call graph reaches this. The branch exists so
    # that a caller which does reach it is not told it submitted nothing.
    executed = ExecResult(outcome=Outcome.FINAL_ANSWER, final_answer="the answer")

    assert observation.from_execution(executed) == "the answer"
