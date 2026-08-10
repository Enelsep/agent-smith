from __future__ import annotations

from typing import TYPE_CHECKING

from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

if TYPE_CHECKING:
    import pytest

# Adversarial code: blocks SIGALRM at the OS level, so the soft timeout can
# never fire and the parent must hard-kill the worker.
#
# The earlier try/except-swallow version was flaky: the alarm often fired on
# the `while` line rather than inside the tiny try body, and the soft layer
# caught it. Masking the signal removes the race entirely. `signal` has to be
# allowlisted for this test only -- in production the import guard refuses it,
# which is precisely why this is a defence-in-depth check.
BLOCK_ALARM = (
    "import signal\n"
    "signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})\n"
    "while True:\n    pass\n"
)


def test_variable_persists_across_calls() -> None:
    with Sandbox(timeout=2.0) as sb:
        assert sb.execute("x = 41").outcome is Outcome.OK
        r = sb.execute("print(x + 1)")
        assert r.outcome is Outcome.OK
        assert r.stdout.strip() == "42"


def test_function_persists_across_calls() -> None:
    with Sandbox(timeout=2.0) as sb:
        assert sb.execute("def double(n):\n    return n * 2\n").outcome is Outcome.OK
        r = sb.execute("x = 41\nprint(double(x))")
        assert r.outcome is Outcome.OK
        assert r.stdout.strip() == "82"


def test_a_bare_expression_shows_its_value() -> None:
    # A tool returns its result rather than printing it. Measured on a real
    # SWE-bench run, forgetting the `print()` cost three of eight iterations:
    # the tool ran, the model was shown nothing, and it spent a turn retrying
    # the identical call wrapped in `print`.
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute("def tool():\n    return 'file contents'\n\ntool()")

        assert r.outcome is Outcome.OK
        assert r.stdout.strip() == "file contents"


def test_a_value_shown_is_not_repr_quoted() -> None:
    # What comes back is file contents and test output, meant to be read. The
    # default hook's `repr` would hand the model `'line\nline'` on one line.
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute("'first\\nsecond'")

        assert r.stdout.strip() == "first\nsecond"


def test_statements_that_are_not_expressions_stay_silent() -> None:
    # Only a bare expression echoes. An assignment printing its value would
    # flood the observation and charge the run for it.
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute("x = 41\ny = x + 1\nNone")

        assert r.outcome is Outcome.OK
        assert r.stdout.strip() == ""


def test_a_syntax_error_is_reported_like_any_other_failure() -> None:
    # The block is parsed before it runs now, so a broken one must still come
    # back as an observation rather than taking the worker down.
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute("def broken(:\n    pass\n")

        assert r.outcome is Outcome.ERROR
        assert "SyntaxError" in (r.error or "")


def test_normal_exception_is_captured_not_raised() -> None:
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute("1 / 0")
        assert r.outcome is Outcome.ERROR
        assert r.error is not None
        assert "ZeroDivisionError" in r.error


def test_namespace_survives_exception() -> None:
    with Sandbox(timeout=2.0) as sb:
        sb.execute("x = 41")
        sb.execute("1 / 0")
        r = sb.execute("print('x is', x)")
        assert r.outcome is Outcome.OK
        assert r.stdout.strip() == "x is 41"


def test_soft_timeout_keeps_partial_output_and_namespace() -> None:
    with Sandbox(timeout=2.0) as sb:
        sb.execute("x = 41")
        r = sb.execute("print('partial')\nwhile True:\n    pass\n")
        assert r.outcome is Outcome.SOFT_TIMEOUT
        assert "partial" in r.stdout
        survived = sb.execute("print('x is still', x)")
        assert survived.outcome is Outcome.OK
        assert survived.stdout.strip() == "x is still 41"


def test_hard_timeout_restarts_reports_duration_and_loses_namespace() -> None:
    with Sandbox(timeout=2.0, authorized_imports=["signal"]) as sb:
        sb.execute("x = 41")
        r = sb.execute(BLOCK_ALARM)
        assert r.outcome is Outcome.HARD_TIMEOUT
        # Regression: the parent-side wait must be timed, not reported as 0.0.
        assert r.duration_ms > 0
        assert sb.restarts == 1
        lost = sb.execute("print(x)")
        assert lost.outcome is Outcome.ERROR
        assert lost.error is not None
        assert "NameError" in lost.error


def test_worker_crash_reports_crashed_not_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The security layer blocks the imports that could kill the worker from
    # user code, so we simulate the worker dying after send() but before it
    # replies: recv() then hits EOF, which must read as CRASHED, not a timeout.
    with Sandbox(timeout=2.0) as sb:
        sb.execute("x = 1")  # spin up a live worker
        assert sb._proc is not None and sb._proc.is_alive()

        def _dead_pipe() -> object:
            raise EOFError

        monkeypatch.setattr(sb._conn, "recv", _dead_pipe)
        r = sb.execute("x = 2")
        # A dead worker must be distinguished from a hung one.
        assert r.outcome is Outcome.CRASHED
        assert sb.restarts == 1
        # restart() swapped in a fresh connection, so the sandbox works again.
        assert sb.execute("print('alive')").outcome is Outcome.OK


def test_worker_killed_between_calls_flags_namespace_reset() -> None:
    with Sandbox(timeout=2.0) as sb:
        assert sb.execute("x = 41").namespace_reset is False  # healthy call
        assert sb._proc is not None

        # Simulate an external kill / SBX-5 memory-limit death between calls.
        sb._proc.kill()
        sb._proc.join(timeout=2)
        assert not sb._proc.is_alive()

        # The code still runs, but the fresh namespace loss must be surfaced.
        r = sb.execute("print('fresh start')")
        assert r.outcome is Outcome.OK
        assert r.namespace_reset is True
        assert sb.restarts == 1

        # The state really is gone, and the recovery call does NOT re-flag it.
        lost = sb.execute("print(x)")
        assert lost.outcome is Outcome.ERROR
        assert lost.error is not None
        assert "NameError" in lost.error
        assert lost.namespace_reset is False


def test_final_answer_unwinds_the_loop() -> None:
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute("y = 7\nfinal_answer(f'the answer is {y}')")
        assert r.outcome is Outcome.FINAL_ANSWER
        assert r.final_answer == "the answer is 7"


def test_final_answer_cannot_be_swallowed_by_broad_except() -> None:
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute(
            "try:\n"
            "    final_answer('real answer')\n"
            "except Exception:\n"
            "    print('swallowed the final answer')\n"
        )
        assert r.outcome is Outcome.FINAL_ANSWER
        assert r.final_answer == "real answer"


def test_system_exit_is_forwarded_and_sandbox_stays_usable() -> None:
    with Sandbox(timeout=2.0) as sb:
        r = sb.execute("raise SystemExit(3)")
        assert r.outcome is Outcome.SHUTDOWN
        assert r.error is not None
        assert "SystemExit" in r.error
        after = sb.execute("print('alive')")
        assert after.outcome is Outcome.OK
        assert after.stdout.strip() == "alive"
