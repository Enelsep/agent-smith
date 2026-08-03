"""One task, run as a Thought -> Code -> Observation cycle."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

from agent_smith.agent import observation
from agent_smith.agent.budget import (
    FORCED_SUBMISSION_NUDGE,
    can_attempt_submission,
    capped_max_tokens,
    estimate_tokens,
    output_reserve,
    remaining_output_tokens,
    should_force_submission,
)
from agent_smith.extraction import extract_code
from agent_smith.llm import LLMResponse, Message, ProviderError
from agent_smith.models.contract import SolutionOutput, StepMetrics
from agent_smith.sandbox.protocol import ExecResult, Outcome

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_smith.agent.task import TaskSpec
    from agent_smith.llm import LLMProvider

# The MBPP ceiling the moulinette enforces, and the stricter of the two it
# knows: SWE-bench allows 30 and its CLI passes that. A caller who forgets
# cannot silently invalidate a run.
DEFAULT_MAX_ITERATIONS = 10

DEFAULT_MAX_INPUT_TOKENS = 6000
"""MBPP's cumulative input-token ceiling, the stricter of the two the
subject enforces (SWE-bench allows 300 000). Same reasoning as
DEFAULT_MAX_ITERATIONS: a caller that forgets to override it for
SWE-bench cannot silently invalidate a run by undershooting.
"""

DEFAULT_MAX_OUTPUT_TOKENS = 1500
"""MBPP's cumulative output-token ceiling (SWE-bench allows 10 000)."""

DEFAULT_MAX_WALL_CLOCK_SECONDS = 120.0
"""MBPP's wall-clock ceiling in seconds (SWE-bench allows 900). The 15%
safety margin should_force_submission applies is computed from this
value at call time, not baked into it.
"""


class Sandbox(Protocol):
    """The sandbox interface the loop drives.

    Structural, like `LLMProvider`, rather than the concrete
    `agent_smith.sandbox.process.Sandbox`: a test fake satisfies it without
    inheriting a class whose constructor spawns a subprocess, and this module
    never names `agent_smith.sandbox.process`, so nothing here can pull in
    `multiprocessing` even transitively.
    """

    restarts: int

    def execute(self, code: str) -> ExecResult: ...


def _unchanged(messages: list[Message]) -> list[Message]:
    """The CORE-7 seam, until CORE-7 fills it."""
    return messages


def run_task(
    task: TaskSpec,
    provider: LLMProvider,
    sandbox: Sandbox,
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    compact: Callable[[list[Message]], list[Message]] = _unchanged,
    clock: Callable[[], float] = time.monotonic,
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_wall_clock_seconds: float = DEFAULT_MAX_WALL_CLOCK_SECONDS,
    max_tokens_per_call: int | None = None,
) -> SolutionOutput:
    """Run one task to a `SolutionOutput`. Never raises.

    A crash scores as an automatic fail, so every failure path returns a valid
    result with `success=False` and a populated `error` instead.

    The sandbox is driven here but built and disposed of by the caller: that is
    what lets a fake satisfy it, and it puts the `finally` that must survive a
    signal at the process entry point rather than inside a library.

    `max_tokens_per_call` is the per-request ceiling the operator configured —
    `ResolvedConfig.max_tokens`, from `models.json`. The loop lowers it as the
    cumulative output budget drains but never raises it, so a model configured
    to answer in 400 tokens is not handed 1 500 on the first step. `None`
    leaves the provider's own default in force.
    """
    run = _Run(task, clock)
    try:
        run.execute(
            provider,
            sandbox,
            max_iterations,
            compact,
            max_input_tokens,
            max_output_tokens,
            max_wall_clock_seconds,
            max_tokens_per_call,
        )
        return run.to_solution()
    except Exception as unexpected:  # noqa: BLE001 - the boundary is the point
        run.error = f"the agent loop failed: {unexpected}"
        return run.to_solution()


class _Run:
    """The state one run accumulates.

    It lives outside the boundary in `run_task` so that a failure at iteration
    seven still reports the first six.
    """

    def __init__(self, task: TaskSpec, clock: Callable[[], float]) -> None:
        self._task = task
        self._clock = clock
        self._started = clock()
        self.history: list[Message] = [
            {"role": "system", "content": task.system_prompt},
            {"role": "user", "content": task.task_prompt},
        ]
        self.steps: list[StepMetrics] = []
        self.solution = ""
        self.success = False
        self.error: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def execute(
        self,
        provider: LLMProvider,
        sandbox: Sandbox,
        max_iterations: int,
        compact: Callable[[list[Message]], list[Message]],
        max_input_tokens: int,
        max_output_tokens: int,
        max_wall_clock_seconds: float,
        max_tokens_per_call: int | None,
    ) -> None:
        """Turn the loop until an answer arrives, the budget runs out, or the
        iterations run out."""
        for step in range(1, max_iterations + 1):
            remaining_output = remaining_output_tokens(
                self.total_output_tokens, max_output_tokens
            )
            view = compact(self.history)
            reason = should_force_submission(
                total_input_tokens=self.total_input_tokens,
                estimated_next_input=estimate_tokens(view),
                max_input_tokens=max_input_tokens,
                elapsed_seconds=self._clock() - self._started,
                max_wall_clock_seconds=max_wall_clock_seconds,
                remaining_output_tokens=remaining_output,
                reserved_output_tokens=output_reserve(max_output_tokens),
            )
            if reason is not None:
                if not can_attempt_submission(remaining_output):
                    # Reported as an output_tokens stop whatever tripped the
                    # guard: the label names the budget that blocked the
                    # attempt, which is what a failure-category breakdown
                    # needs to know.
                    self.error = (
                        f"stopped by the output_tokens budget guard before "
                        f"step {step}: {remaining_output} output tokens "
                        "remained, too few to attempt a final answer"
                    )
                    return
                # Added to the view, not to the transcript. The forced
                # iteration is the last one, so nothing reads this message
                # back, and it keeps `compact` to one call per iteration:
                # CORE-7 need not be idempotent, nor preserve the last
                # message it is handed.
                view = [*view, {"role": "user", "content": FORCED_SUBMISSION_NUDGE}]
            try:
                answer = provider.complete(
                    view,
                    max_tokens=capped_max_tokens(max_tokens_per_call, remaining_output),
                )
            except ProviderError as refused:
                # CORE-2 has already retried across the key pool and spent its
                # budget. Retrying here would stack two policies and burn the
                # task's wall clock; the message is kept as it came.
                self.error = str(refused)
                return
            self.history.append({"role": "assistant", "content": answer.text})
            extracted = extract_code(answer.text, step=step)
            if extracted.code is None:
                # Nothing ran, so both sandbox fields stay empty: `StepMetrics`
                # documents that as correct for a step with no execution.
                said = observation.from_extraction(extracted)
                self._record(step, answer, sandbox_input="", sandbox_output="")
            else:
                before = sandbox.restarts
                executed = sandbox.execute(extracted.code)
                if executed.outcome is Outcome.FINAL_ANSWER and executed.final_answer:
                    self.solution = executed.final_answer
                    self.success = True
                    self._record(
                        step,
                        answer,
                        extracted.code,
                        observation.combined_output(executed),
                    )
                    return
                said = observation.from_execution(
                    executed,
                    namespace_lost=sandbox.restarts != before,
                    repair_note=extracted.repair_note,
                )
                self._record(step, answer, extracted.code, said)
            if reason is not None:
                self.error = (
                    f"stopped by the {reason} budget guard at step {step}; "
                    "no final answer was received"
                )
                return
            self.history.append({"role": "user", "content": said})
        self.error = (
            f"the agent used all {max_iterations} iterations without calling "
            "final_answer()"
        )

    def _record(
        self,
        step: int,
        answer: LLMResponse,
        sandbox_input: str,
        sandbox_output: str,
    ) -> None:
        self.steps.append(
            StepMetrics(
                step=step,
                input_tokens=answer.input_tokens,
                output_tokens=answer.output_tokens,
                request_time_ms=answer.latency_ms,
                api_url=answer.api_url,
                model_name=answer.model,
                llm_output=answer.text,
                sandbox_input=sandbox_input,
                sandbox_output=sandbox_output,
                retries=answer.retries,
            )
        )
        self.total_input_tokens += answer.input_tokens
        self.total_output_tokens += answer.output_tokens

    def to_solution(self) -> SolutionOutput:
        """Everything the run accumulated, in the shape the moulinette reads."""
        return SolutionOutput(
            task_id=self._task.task_id,
            benchmark=self._task.benchmark,
            success=self.success,
            solution=self.solution,
            iterations=len(self.steps),
            total_requests=sum(1 + step.retries for step in self.steps),
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            total_time_seconds=self._clock() - self._started,
            steps=self.steps,
            system_prompt=self._task.system_prompt,
            error=self.error,
        )
