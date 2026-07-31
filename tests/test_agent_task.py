"""What a caller must say about the task to be solved."""

import pytest
from pydantic import ValidationError

from agent_smith.agent.task import TaskSpec


def a_task(**overrides: str) -> TaskSpec:
    fields = {
        "task_id": "11",
        "benchmark": "mbpp",
        "system_prompt": "You are a careful Python programmer.",
        "task_prompt": "Write a function that adds two numbers.",
    }
    fields.update(overrides)
    return TaskSpec(**fields)


def test_it_carries_the_four_things_a_run_needs() -> None:
    task = a_task()

    assert task.task_id == "11"
    assert task.benchmark == "mbpp"
    assert task.system_prompt == "You are a careful Python programmer."
    assert task.task_prompt == "Write a function that adds two numbers."


@pytest.mark.parametrize(
    "missing", ["task_id", "benchmark", "system_prompt", "task_prompt"]
)
def test_every_field_is_required(missing: str) -> None:
    fields = {
        "task_id": "11",
        "benchmark": "mbpp",
        "system_prompt": "sys",
        "task_prompt": "task",
    }
    del fields[missing]

    with pytest.raises(ValidationError):
        TaskSpec(**fields)


def test_a_task_cannot_be_edited_while_the_loop_turns() -> None:
    task = a_task()

    with pytest.raises(ValidationError):
        # setattr rather than assignment: the mypy pydantic plugin is not
        # enabled, so a direct assignment would need a `type: ignore` that
        # mypy then reports as unused.
        setattr(task, "task_id", "12")  # noqa: B010
