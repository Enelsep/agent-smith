"""Reading a finished run to see where its input budget went."""

from __future__ import annotations

from agent_smith.agent.report import budget_report
from agent_smith.models.contract import SolutionOutput, StepMetrics


def a_step(step: int, *, code: str, observation: str, tokens: int) -> StepMetrics:
    return StepMetrics(
        step=step,
        input_tokens=tokens,
        output_tokens=40,
        request_time_ms=10.0,
        sandbox_input=code,
        sandbox_output=observation,
    )


A_MANUAL = (
    "## Tools\n\n"
    "- `read_file(filepath: str)` - read a file\n"
    "- `run_tests(eval_script: str)` - run the tests\n"
)


def a_run(*steps: StepMetrics, system_prompt: str = A_MANUAL) -> SolutionOutput:
    return SolutionOutput(
        task_id="sympy__sympy-14711",
        benchmark="swebench",
        success=False,
        solution="",
        iterations=len(steps),
        total_requests=len(steps),
        total_input_tokens=sum(s.input_tokens for s in steps),
        total_output_tokens=sum(s.output_tokens for s in steps),
        total_time_seconds=1.0,
        steps=list(steps),
        system_prompt=system_prompt,
    )


def test_it_names_the_tool_each_step_called() -> None:
    # "Fix the tools, not the prompt" (SWE-6) needs to know which tool produced
    # the observation that cost the budget, not just that a step was expensive.
    run = a_run(
        a_step(1, code="print(read_file('a.py'))", observation="x" * 400, tokens=500),
        a_step(2, code="print(run_tests(s, d))", observation="y" * 8000, tokens=2500),
    )

    report = budget_report(run)

    assert "read_file" in report
    assert "run_tests" in report


def test_the_costliest_observation_is_findable() -> None:
    run = a_run(
        a_step(1, code="print(read_file('a.py'))", observation="x" * 400, tokens=500),
        a_step(2, code="print(run_tests(s, d))", observation="y" * 8000, tokens=2500),
    )

    report = budget_report(run)

    assert "2000" in report, "the 8000-char observation should be shown in tokens"


def test_a_step_that_ran_no_code_is_still_a_row() -> None:
    # A step with no code still spent input tokens, and a report that hides it
    # cannot account for the total.
    run = a_run(a_step(1, code="", observation="", tokens=700))

    report = budget_report(run)

    assert "700" in report


def test_a_run_with_no_steps_says_so_rather_than_rendering_a_header() -> None:
    assert "no steps" in budget_report(a_run()).lower()


def test_a_function_the_model_defines_is_not_reported_as_a_tool() -> None:
    # `def solve(` looks exactly like a call to a regex. Naming it would send
    # anyone reading the report after a tool that does not exist.
    run = a_run(
        a_step(
            1,
            code="def solve(n):\n    return n\nprint(read_file('a.py'))",
            observation="x",
            tokens=100,
        )
    )

    assert "read_file" in budget_report(run)
    assert "solve" not in budget_report(run)


def test_only_the_tools_the_run_actually_had_are_named() -> None:
    # `range` and `gcd` are the model's own calls. The tools a run had are
    # listed in the manual its system prompt carries, so the report reads them
    # from there instead of guessing which names look like tools.
    run = a_run(
        a_step(
            1,
            code="print(range(3), read_file('a.py'))",
            observation="x",
            tokens=100,
        )
    )
    report = budget_report(run)

    assert "read_file" in report
    assert "range" not in report


def test_a_run_with_no_tools_at_all_claims_none() -> None:
    run = a_run(
        a_step(1, code="print(gcd(4, 6))", observation="2", tokens=100),
        system_prompt="no manual here",
    )

    assert "gcd" not in budget_report(run)
