from __future__ import annotations

import re
import sys
from pathlib import Path

from agent_smith.agent.budget import CHARS_PER_TOKEN
from agent_smith.models.contract import SolutionOutput

_CALL = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(")
_DEFINITION = re.compile(r"\b(?:def|class)\s+\w+\s*\(?")
_MANUAL_ENTRY = re.compile(r"^- `([a-z_][a-z0-9_]*)\(", re.MULTILINE)


def tools_available(system_prompt: str) -> frozenset[str]:
    """The tool names the run's own manual documents.

    Read from the artefact rather than assumed: the manual is generated from
    the connected server, so this is true of whatever server that run had —
    and empty for MBPP, which has no tools at all.
    """
    return frozenset(_MANUAL_ENTRY.findall(system_prompt or ""))


def _tool_called(code: str, tools: frozenset[str]) -> str:
    """The first tool the step's code calls, or "-" when it calls none.

    Definitions are stripped before the scan: `def solve(` is indistinguishable
    from a call to a regex, and reporting the model's own helper as the costly
    tool sends the reader after something that does not exist.
    """
    for name in _CALL.findall(_DEFINITION.sub("", code or "")):
        if name in tools:
            return str(name)
    return "-"


def budget_report(solution: SolutionOutput) -> str:
    """One line per step: what it called, what it cost, what came back."""
    if not solution.steps:
        return f"{solution.task_id}: no steps recorded."

    tools = tools_available(solution.system_prompt)
    header = (
        f"{solution.task_id} ({solution.benchmark}) - "
        f"{solution.total_input_tokens} input tokens over {len(solution.steps)} steps"
    )
    lines = [header, f"{'step':>4} {'tool':<28} {'input':>7} {'observation':>12}"]
    for step in solution.steps:
        observation = len(step.sandbox_output or "") // CHARS_PER_TOKEN
        lines.append(
            f"{step.step:>4} {_tool_called(step.sandbox_input, tools):<28} "
            f"{step.input_tokens:>7} {observation:>12}"
        )
    return "\n".join(lines)


_SUMMARY = re.compile(r"Summary:\s*(\d+)\s+passed,\s*(\d+)\s+failed")


def failure_curve(solution: SolutionOutput) -> list[tuple[int, int]]:
    """`(step, failing tests)` for every step whose observation reports a run.

    Read off the `Summary:` line `run_tests` writes, so a step that ran nothing
    contributes nothing: the curve is the sequence of measurements the agent
    actually took, not one point per iteration.
    """
    seen: list[tuple[int, int]] = []
    for step in solution.steps:
        found = _SUMMARY.search(step.sandbox_output or "")
        if found:
            seen.append((step.step, int(found.group(2))))
    return seen


def first_drop(curve: list[tuple[int, int]]) -> int | None:
    """The step where failures first fall below the run's own first reading.

    The subject's second intermediary metric. The baseline is the first count
    the run measured rather than a fixed number, because what the suite reports
    before any edit is a property of the task, not of the agent.

    `None` covers both a run that never measured twice and one that never
    improved -- the report has to tell those apart from the curve itself, and
    a sentinel step number would hide the difference.
    """
    if len(curve) < 2:
        return None
    baseline = curve[0][1]
    for step, failing in curve[1:]:
        if failing < baseline:
            return step
    return None


def failure_report(solution: SolutionOutput) -> str:
    """When the failures started dropping, and what they did on the way."""
    curve = failure_curve(solution)
    if not curve:
        return f"{solution.task_id}: no test run reported a summary."
    drop = first_drop(curve)
    trail = " -> ".join(f"{step}:{failing}" for step, failing in curve)
    when = "never below its baseline" if drop is None else f"first drop at step {drop}"
    return f"{solution.task_id}: {when} (failing by step: {trail})"


def main() -> None:
    """Print the report for each solution file named on the command line."""
    for path in sys.argv[1:]:
        raw = Path(path).read_text(encoding="utf-8")
        solution = SolutionOutput.model_validate_json(raw)
        print(budget_report(solution))
        print(failure_report(solution))
        print()


if __name__ == "__main__":
    main()
