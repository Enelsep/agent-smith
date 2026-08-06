"""Where a finished run spent its input budget.

SWE-bench allows 300 000 cumulative input tokens and every observation stays in
the transcript, so the cost of a run is dominated by what the tools hand back.
This reads a `SolutionOutput` and says which tool produced what, so SWE-6 can
fix the tool rather than guess at the prompt.

Run it on a solution file:

    uv run python -m agent_smith.agent.report path/to/solution.json
"""

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


def main() -> None:
    """Print the report for each solution file named on the command line."""
    for path in sys.argv[1:]:
        raw = Path(path).read_text(encoding="utf-8")
        print(budget_report(SolutionOutput.model_validate_json(raw)))
        print()


if __name__ == "__main__":
    main()
