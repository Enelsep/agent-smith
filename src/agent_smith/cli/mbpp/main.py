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

from agent_smith.agent.loop import run_task
from agent_smith.agent.task import TaskSpec
from agent_smith.cli.mbpp.prompt import build_system_prompt, task_prompt
from agent_smith.config import ConfigError, ResolvedConfig, resolve_config
from agent_smith.llm.keypool import KeyPool
from agent_smith.llm.retry import RetryingProvider
from agent_smith.models.contract import MBPPTaskInput, SolutionOutput
from agent_smith.sandbox.process import Sandbox

BENCHMARK = "mbpp"

# M1 runs with the limits off: the milestone is "one task solved end to end",
# and a run that dies on iteration ten says nothing about whether the loop
# works. `run_task` defaults to the exam budget (6000/1500/120s), so the
# ceilings must be raised explicitly too. MBPP-3 brings all of this back to
# the exam values.
#
# These are cumulative ceilings only. The per-call `max_tokens` stays the one
# `models.json` configures, passed as `max_tokens_per_call`: without it the
# loop offers the whole remaining output budget to every single request.
M1_MAX_ITERATIONS = 25
M1_MAX_INPUT_TOKENS = 1_000_000
M1_MAX_OUTPUT_TOKENS = 250_000
M1_MAX_WALL_CLOCK_SECONDS = 1800.0

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
    parser.add_argument("--max-iterations", default=M1_MAX_ITERATIONS, type=int)
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


def failed_run(task_id: str, error: str) -> SolutionOutput:
    """A valid solution for a run that never got as far as an iteration."""
    return SolutionOutput(
        task_id=task_id,
        benchmark=BENCHMARK,
        success=False,
        solution="",
        iterations=0,
        total_requests=0,
        total_input_tokens=0,
        total_output_tokens=0,
        total_time_seconds=0.0,
        error=error,
    )


def write_solution(path: Path, solution: SolutionOutput) -> None:
    """Write the solution to the requested path, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(solution.model_dump_json(indent=2), encoding="utf-8")


def _provider(config: ResolvedConfig) -> RetryingProvider:
    """Build the provider stack.

    Imported here rather than at module scope: `openai_compat` is the only
    module that pulls in an HTTP client, and `tests/test_llm_import_boundary.py`
    checks that the rest of the tree does not.
    """
    from agent_smith.llm.openai_compat import OpenAICompatProvider

    pool = KeyPool(config.api_keys)
    inner = OpenAICompatProvider(
        config.base_url,
        config.model_name,
        pool,
        stop=config.stop,
        max_tokens=config.max_tokens,
    )
    return RetryingProvider(inner, pool)


def solve(args: argparse.Namespace) -> SolutionOutput:
    """Run one task to a solution. Never raises."""
    try:
        task = load_task(args.task_file)
    except ConfigError as unusable:
        return failed_run(UNKNOWN_TASK_ID, str(unusable))

    task_id = str(task.task_id)
    try:
        config = resolve_config(
            provider_url=args.provider_url,
            model_name=args.model_name,
            env_file=args.env_file,
        )
    except ConfigError as unresolved:
        return failed_run(task_id, str(unresolved))

    spec = build_task_spec(task, build_system_prompt(config.sandbox.authorized_imports))
    try:
        with Sandbox(
            timeout=config.sandbox.max_execution_time_seconds,
            authorized_imports=config.sandbox.authorized_imports,
        ) as sandbox:
            return run_task(
                spec,
                _provider(config),
                sandbox,
                max_iterations=args.max_iterations,
                max_input_tokens=M1_MAX_INPUT_TOKENS,
                max_output_tokens=M1_MAX_OUTPUT_TOKENS,
                max_wall_clock_seconds=M1_MAX_WALL_CLOCK_SECONDS,
                max_tokens_per_call=config.max_tokens,
            )
    except Exception as unexpected:  # noqa: BLE001 - the boundary is the point
        return failed_run(task_id, f"the run could not start: {unexpected}")


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
