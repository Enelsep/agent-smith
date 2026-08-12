from __future__ import annotations

from collections.abc import Sequence

from agent_smith.models.contract import MBPPTaskInput
from agent_smith.prompts import load_prompt


def build_system_prompt(authorized_imports: Sequence[str]) -> str:
    """The system prompt, quoting the allowlist the sandbox will actually enforce.

    The list is passed in rather than hardcoded
    """
    return load_prompt("mbpp").replace("{imports}", ", ".join(authorized_imports))


def task_prompt(task: MBPPTaskInput) -> str:
    """The one task, in the order a solver needs it: what, then how, then proof.

    `test_list` carries the public assertions only, and a solution is judged on
    the full set. The visible ones are therefore labelled as a subset rather
    than presented as the specification, so the model solves the description
    instead of fitting the examples.
    """
    parts = [
        task.task_definition,
        "",
        "Your function must use exactly this signature:",
        task.function_definition,
    ]
    if task.test_imports:
        parts += ["", "The tests run with these imports:", *task.test_imports]
    if task.test_list:
        parts += [
            "",
            "Visible tests - a subset, hidden tests also run:",
            *task.test_list,
        ]
    return "\n".join(parts)
