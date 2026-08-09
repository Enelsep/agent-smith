"""The `agent_swebench` command line entry point.

Loads a task file, runs the agent against the repository inside the task's
Docker image, and writes a `SolutionOutput` whose solution is the git patch.
A crash scores as an automatic fail, so every path here ends in a written file.

The tools run inside the container, not on this machine: `mcp_tools_swebench.py`
is copied in and spoken to over `docker exec -i`, which is what lets it read and
edit the checked-out repository at all.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_smith.agent.loop import AnswerValidator, judged_once, run_task
from agent_smith.agent.task import TaskSpec
from agent_smith.cli import common
from agent_smith.cli.common import build_provider, failed_run, write_solution
from agent_smith.cli.sandbox.mcp_bridge import MCPBridge
from agent_smith.cli.swebench.prompt import build_system_prompt, task_prompt
from agent_smith.config import ConfigError, resolve_config
from agent_smith.mcp.client import UnifiedMCPClient
from agent_smith.models.contract import SolutionOutput, SWEBenchTaskInput
from agent_smith.sandbox.process import Sandbox
from agent_smith.tools.run_tests import PASSED_STATUS
from agent_swebench.docker import DockerManager

BENCHMARK = "swebench"

# The four limits of the subject for SWE-bench (VI.1.2). Every one of them is
# wider than MBPP's, and `run_task` defaults to MBPP's, so they have to be
# passed rather than relied on.
MAX_ITERATIONS = 30
MAX_INPUT_TOKENS = 300_000
MAX_OUTPUT_TOKENS = 10_000
MAX_WALL_CLOCK_SECONDS = 900.0

SERVER_SOURCE = Path(__file__).resolve().parents[4] / "mcp_tools_swebench.py"
SERVER_IN_CONTAINER = "/mcp_tools_swebench.py"

# The server is a thin dispatcher over `agent_smith.tools`, so the package has
# to travel with it. Nothing under `tools` imports beyond the standard library,
# an optional `jedi` and one sibling module, so the copy needs no install and no
# dependency of ours inside the image.
PACKAGE_SOURCE = Path(__file__).resolve().parents[2]
PACKAGE_PARENT_IN_CONTAINER = "/"

# The task's evaluation script, carried into the container so `run_tests()`
# needs no argument. Asking the model for it cost us every failure mode a
# 2 000-character retype can have: one dropped a heredoc and bash died at EOF,
# one passed `/bin/bash` and stalled the tool for a full timeout, one truncated
# it past the output markers and got a verdict by accident.
EVAL_SCRIPT_IN_CONTAINER = "/eval_script.sh"

UNKNOWN_TASK_ID = "unknown"


REFUSED = (
    "final_answer refused: run against the task's own evaluation script, the "
    "repository as you left it did not pass.\n\n{failure}\n\nFix the code, run "
    "the tests until they pass, then submit again."
)


def build_validator(
    task: SWEBenchTaskInput,
    call_tool: Callable[[str, dict[str, Any]], str],
    testbed: str,
) -> AnswerValidator:
    """Refuse a patch the task's own evaluation script does not accept.

    MBPP-6 gave the loop the last say on a submission; this is the same idea
    where the answer is a diff. A diff cannot be executed, but the repository
    that produced it can: running the script here judges the state the patch
    describes, which is what the grader will do with it.

    The script runs through the tool bridge rather than on this machine, for
    the reason the whole server runs in the container -- the checkout only
    exists in there.

    It ends by restoring the test files from git, so a model that edited them
    to make its own life easier is judged on the real ones, and the working
    tree is left as the submitted diff described it.

    A run refused here is not lost: the loop keeps the last refused submission,
    so a false refusal -- an evaluation slower than the tool's own timeout, say
    -- costs iterations rather than the answer.
    """

    def validate(submitted: str) -> str | None:
        if not task.eval_script.strip():
            # Nothing to judge against. Refusing every answer would be worse
            # than accepting one that was never checked.
            return None
        ran = call_tool("run_tests", {"directory": testbed})
        if PASSED_STATUS in ran:
            return None
        return REFUSED.format(failure=ran)

    return validate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """The flags from the subject, with the model pair left optional."""
    parser = argparse.ArgumentParser(prog="agent_swebench")
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider-url", default=None)
    parser.add_argument("--env-file", default=None, type=Path)
    parser.add_argument("--max-iterations", default=MAX_ITERATIONS, type=int)
    return parser.parse_args(argv)


def load_task(path: Path) -> SWEBenchTaskInput:
    """Read the task file, or say why it could not be read."""
    return common.load_task(path, SWEBenchTaskInput, "SWE-bench")


def build_task_spec(
    task: SWEBenchTaskInput, system_prompt: str, testbed: str = "/testbed"
) -> TaskSpec:
    """Describe the task well enough for the loop to run it."""
    return TaskSpec(
        task_id=task.instance_id,
        benchmark=BENCHMARK,
        system_prompt=system_prompt,
        task_prompt=task_prompt(task, testbed),
    )


def _failed(task_id: str, error: str) -> SolutionOutput:
    """`failed_run` with this CLI's benchmark already filled in."""
    return failed_run(BENCHMARK, task_id, error)


def remaining_wall_clock(spent_seconds: float) -> float:
    """What is left of the 900 seconds once `spent_seconds` are gone.

    The limit is on the task, not on the loop. Pulling a SWE-bench image and
    starting its container is part of solving the task and can run to minutes,
    all of it before `run_task` sees anything — so the loop is handed the
    remainder rather than the whole. Never negative: a setup that already
    overran hands zero, which the guard reads as no budget left rather than as
    an unlimited one.
    """
    return max(0.0, MAX_WALL_CLOCK_SECONDS - spent_seconds)


def solve(args: argparse.Namespace) -> SolutionOutput:
    """Run one task to a solution. Never raises."""
    # Everything below counts against the wall clock, including the image pull.
    # Interpreter startup happens before this line and is not ours to measure;
    # it is seconds against a ceiling of nine hundred.
    started = time.monotonic()
    try:
        task = load_task(args.task_file)
    except ConfigError as unusable:
        return _failed(UNKNOWN_TASK_ID, str(unusable))

    try:
        config = resolve_config(
            provider_url=args.provider_url,
            model_name=args.model_name,
            benchmark=BENCHMARK,
            env_file=args.env_file,
        )
    except ConfigError as unresolved:
        return _failed(task.instance_id, str(unresolved))

    try:
        with contextlib.ExitStack() as session:
            container = DockerManager(
                task.docker_image, f"agent-smith-{task.instance_id}"
            )
            container.start()
            session.callback(container.cleanup)
            container.copy_in(SERVER_SOURCE, SERVER_IN_CONTAINER)
            container.copy_in(PACKAGE_SOURCE, PACKAGE_PARENT_IN_CONTAINER)

            if task.eval_script.strip():
                script = (
                    Path(session.enter_context(tempfile.TemporaryDirectory()))
                    / "eval_script.sh"
                )
                script.write_text(task.eval_script, encoding="utf-8")
                container.copy_in(script, EVAL_SCRIPT_IN_CONTAINER)

            # Where the checkout actually is, asked of the image rather than
            # assumed: the tools all default to the working directory, and the
            # server reads TESTBED_PATH. Both are set from the one answer, so
            # neither the model nor a tool has to guess.
            testbed = container.locate_testbed()

            # ponytail: assumes `python` on PATH inside the image. SWE-5 can
            # name an interpreter path here if some image needs one.
            bridge = MCPBridge(
                UnifiedMCPClient(
                    command="docker",
                    args=[
                        "exec",
                        "-i",
                        "-w",
                        testbed,
                        "-e",
                        f"PYTHONPATH={PACKAGE_PARENT_IN_CONTAINER}",
                        "-e",
                        f"TESTBED_PATH={testbed}",
                        str(container.container_id),
                        "python",
                        SERVER_IN_CONTAINER,
                    ],
                )
            )
            bridge.start()
            session.callback(bridge.close)

            spec = build_task_spec(task, build_system_prompt(bridge.tool_defs), testbed)
            sandbox = session.enter_context(
                Sandbox.from_config(
                    config.sandbox,
                    tool_defs=bridge.tool_defs,
                    tool_handler=bridge.call,
                )
            )
            return run_task(
                spec,
                build_provider(config, wall_clock_seconds=MAX_WALL_CLOCK_SECONDS),
                sandbox,
                max_iterations=args.max_iterations,
                max_input_tokens=MAX_INPUT_TOKENS,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                max_wall_clock_seconds=remaining_wall_clock(time.monotonic() - started),
                max_tokens_per_call=config.max_tokens,
                validate_answer=judged_once(
                    build_validator(task, bridge.call, testbed)
                ),
            )
    except Exception as unexpected:  # noqa: BLE001 - the boundary is the point
        return _failed(task.instance_id, f"the run could not start: {unexpected}")


def main() -> None:
    """Write a solution and return.

    The exit code stays zero whenever a solution was written: an unsolved task
    and a crashed program are different outcomes, and only the file tells them
    apart.
    """
    args = parse_args()
    solution = solve(args)
    try:
        write_solution(args.output, solution)
    except OSError as unwritable:
        print(f"cannot write {args.output}: {unwritable}", file=sys.stderr)
        raise SystemExit(1) from unwritable
