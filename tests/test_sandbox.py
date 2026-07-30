from __future__ import annotations

from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

# Adversarial code: swallows the SIGALRM-driven TimeoutError forever, so the
# soft timeout cannot interrupt it and the parent must hard-kill the worker.
SWALLOW_ALARM = (
    "while True:\n    try:\n        pass\n    except TimeoutError:\n        pass\n"
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
    with Sandbox(timeout=2.0) as sb:
        sb.execute("x = 41")
        r = sb.execute(SWALLOW_ALARM)
        assert r.outcome is Outcome.HARD_TIMEOUT
        # Regression: the parent-side wait must be timed, not reported as 0.0.
        assert r.duration_ms > 0
        assert sb.restarts == 1
        lost = sb.execute("print(x)")
        assert lost.outcome is Outcome.ERROR
        assert lost.error is not None
        assert "NameError" in lost.error


def test_worker_crash_reports_crashed_not_timeout() -> None:
    with Sandbox(timeout=2.0) as sb:
        sb.execute("x = 1")
        r = sb.execute("import os\nos._exit(1)")
        # A dead worker must be distinguished from a hung one.
        assert r.outcome is Outcome.CRASHED
        assert sb.restarts == 1
        # The sandbox recovers and is usable again.
        assert sb.execute("print('alive')").outcome is Outcome.OK


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
