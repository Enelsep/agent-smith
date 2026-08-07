"""What the SWE-bench agent is told: the turn contract, and one issue.

The system prompt lives in `agent_smith/prompts/swebench.md`. Its tool section
is not written there — it is generated from the schemas the connected MCP
server publishes, so changing server changes the documentation with no edit
here and none in the prompt file.
"""

from __future__ import annotations

from agent_smith.mcp.formatter import to_sandbox_manual
from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.models.contract import SWEBenchTaskInput
from agent_smith.prompts import load_prompt


def build_system_prompt(tools: list[MCPToolDefinition]) -> str:
    """The system prompt, documenting the tools this session actually has.

    Substituted rather than formatted: the prompt is a file so its wording can
    be revised without touching Python, and `str.format` would make every brace
    in it a field to resolve.
    """
    return load_prompt("swebench").replace("{tools}", to_sandbox_manual(tools))


def task_prompt(task: SWEBenchTaskInput, testbed: str = "/testbed") -> str:
    """The one issue, in the order a fixer needs it.

    The evaluation script is quoted in full because `run_tests(eval_script,
    directory)` takes it as an argument: a model that has not been given the
    text cannot call the tool that decides whether it is done.

    `testbed` is where the checkout lives inside the container. Every tool
    defaults to the working directory, so a model that has to guess the path
    spends iterations discovering it; naming it once here costs a line.
    """
    parts: list[str] = []
    if task.repo:
        parts.append(f"Repository: {task.repo}")
    parts.append(f"The checkout is at {testbed}, and that is the working directory.")
    parts.append(task.problem_statement)
    if task.hints_text:
        parts += ["", "Hints:", task.hints_text]
    parts += [
        "",
        "Run the tests with run_tests, passing this script:",
        task.eval_script,
    ]
    return "\n".join(parts).strip()
