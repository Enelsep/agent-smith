"""The `agent_mbpp` command line entry point.

Loads a task file, runs the agent loop against it, and writes a
`SolutionOutput`. A crash scores as an automatic fail, so every path through
this module ends in a written solution file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from agent_smith.agent import observation
from agent_smith.agent.loop import (
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_WALL_CLOCK_SECONDS,
    AnswerValidator,
    run_task,
)
from agent_smith.agent.task import TaskSpec
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


# The id we report when the task file itself could not be read, since that is
# where the real id would have come from.
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
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as unreadable:
        raise ConfigError(
            f"cannot read the task file {path}: {unreadable}"
        ) from unreadable

    try:
        return MBPPTaskInput.model_validate_json(raw)
    except ValidationError as malformed:
        raise ConfigError(
            f"{path} is not a valid MBPP task file: {malformed}"
        ) from malformed


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
    """Refuse a submission that does not survive the task's visible tests.

    The prompt asks the model to run the assertions before it submits; nothing
    made it. So the submitted string — not the code the model happened to run
    last — is executed here against those same assertions, which is the closest
    this side can get to what the solution will be judged on.

    The assertions run one at a time, and the failing one is quoted into the
    refusal: the sandbox `exec`s a string, so its traceback can only point at
    `<string>`, and a bare `AssertionError` names nothing the model can act on.

    The worker is restarted first, so the submission is judged alone. The
    namespace the loop has been driving still holds every helper the model
    defined along the way, and a submission that leans on one of them passes
    here and raises `NameError` in front of the grader — the single failure this
    exists to catch.

    A task carrying no visible tests has nothing to check against, and running
    the source on its own would refuse a good answer for printing nothing.
    """

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
            # index 0 is the definition, whose own source the model just wrote
            # and can see; every other block is an assertion it needs named.
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
            # The four limits of the subject, VI.1.1. Passed explicitly rather
            # than left to `run_task`'s defaults so the budget this command runs
            # under is readable here, and read from the loop's constants so
            # there is one place to change them.
            #
            # All four are cumulative. The per-call ceiling is a separate
            # question: `max_tokens_per_call` carries what `models.json`
            # configures, and without it the loop would offer the whole
            # remaining output budget to every single request.
            return run_task(
                spec,
                build_provider(config),
                sandbox,
                max_iterations=args.max_iterations,
                max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                max_wall_clock_seconds=DEFAULT_MAX_WALL_CLOCK_SECONDS,
                max_tokens_per_call=config.max_tokens,
                validate_answer=build_validator(task, sandbox),
            )
    except Exception as unexpected:  # noqa: BLE001 - the boundary is the point
        return _failed(task_id, f"the run could not start: {unexpected}")


def main() -> None:
    """Write a solution and return.

    The exit code stays zero whenever a solution was written. An unsolved task
    and a crashed program are different outcomes, and only the solution file can
    tell them apart; exiting non-zero on an honest failure would collapse the
    two for any caller that reads the status. A solution we could not write is
    the one case worth a non-zero exit, because then there is nothing to read.
    """
    args = parse_args()
    solution = solve(args)
    try:
        write_solution(args.output, solution)
    except OSError as unwritable:
        print(f"cannot write {args.output}: {unwritable}", file=sys.stderr)
        raise SystemExit(1) from unwritable
