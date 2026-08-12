from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_smith.agent import observation
from agent_smith.agent.loop import (
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_WALL_CLOCK_SECONDS,
    AnswerValidator,
    judged_once,
    run_task,
)
from agent_smith.agent.task import TaskSpec
from agent_smith.cli import common
from agent_smith.cli.common import build_provider, failed_run, write_solution
from agent_smith.cli.mbpp.prompt import build_system_prompt, task_prompt
from agent_smith.config import ConfigError, resolve_config
from agent_smith.models.contract import MBPPTaskInput, SolutionOutput
from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

BENCHMARK = "mbpp"


def _failed(task_id: str, error: str) -> SolutionOutput:
    """`failed_run` with this CLI's benchmark already filled in."""
    return failed_run(BENCHMARK, task_id, error)


UNKNOWN_TASK_ID = "unknown"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """The flags from the subject, with the model pair left optional.

    A caller may name neither the model nor the endpoint; `resolve_config`
    already falls back to the catalogue in `models.json`, so requiring them here
    would only turn a working default into a usage error.
    """
    parser = argparse.ArgumentParser(prog="agent_mbpp")
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider-url", default=None)
    parser.add_argument("--env-file", default=None, type=Path)
    parser.add_argument("--max-iterations", default=DEFAULT_MAX_ITERATIONS, type=int)
    return parser.parse_args(argv)


def load_task(path: Path) -> MBPPTaskInput:
    """Read the task file, or say why it could not be read."""
    return common.load_task(path, MBPPTaskInput, "MBPP")


def build_task_spec(task: MBPPTaskInput, system_prompt: str) -> TaskSpec:
    """Describe the task well enough for the loop to run it.

    `MBPPTaskInput.task_id` is an `int` while `TaskSpec` and `SolutionOutput`
    both type it as `str`; the conversion happens here, once.
    """
    return TaskSpec(
        task_id=str(task.task_id),
        benchmark=BENCHMARK,
        system_prompt=system_prompt,
        task_prompt=task_prompt(task),
    )


REFUSED = (
    "final_answer refused: run against the task's own tests, the source you "
    "submitted did not pass them.\n\n{failure}\n\nFix the function, run those "
    "same assertions until they raise nothing, then submit again."
)


def build_validator(task: MBPPTaskInput, sandbox: Sandbox) -> AnswerValidator:
    """Refuse a submission that does not survive the task's visible tests."""

    def validate(submitted: str) -> str | None:
        if not task.test_list:
            return None
        sandbox.restart()
        blocks = ["\n".join([*task.test_imports, submitted]), *task.test_list]
        for index, block in enumerate(blocks):
            ran = sandbox.execute(block)
            if ran.outcome is Outcome.OK:
                continue
            failure = observation.from_execution(ran)
            return REFUSED.format(
                failure=failure if index == 0 else f"{block}\n\n{failure}"
            )
        return None

    return validate


def solve(args: argparse.Namespace) -> SolutionOutput:
    """Run one task to a solution. Never raises."""
    try:
        task = load_task(args.task_file)
    except ConfigError as unusable:
        return _failed(UNKNOWN_TASK_ID, str(unusable))

    task_id = str(task.task_id)
    try:
        config = resolve_config(
            provider_url=args.provider_url,
            model_name=args.model_name,
            benchmark=BENCHMARK,
            env_file=args.env_file,
        )
    except ConfigError as unresolved:
        return _failed(task_id, str(unresolved))

    spec = build_task_spec(task, build_system_prompt(config.sandbox.authorized_imports))
    try:
        with Sandbox(
            timeout=config.sandbox.max_execution_time_seconds,
            authorized_imports=config.sandbox.authorized_imports,
        ) as sandbox:
            return run_task(
                spec,
                build_provider(config),
                sandbox,
                max_iterations=args.max_iterations,
                max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                max_wall_clock_seconds=DEFAULT_MAX_WALL_CLOCK_SECONDS,
                max_tokens_per_call=config.max_tokens,
                validate_answer=judged_once(build_validator(task, sandbox)),
            )
    except Exception as unexpected:  # noqa: BLE001 - the boundary is the point
        return _failed(task_id, f"the run could not start: {unexpected}")


def main() -> None:
    """Write a solution and return."""
    args = parse_args()
    solution = solve(args)
    try:
        write_solution(args.output, solution)
    except OSError as unwritable:
        print(f"cannot write {args.output}: {unwritable}", file=sys.stderr)
        raise SystemExit(1) from unwritable
