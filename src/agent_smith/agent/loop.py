"""One task, run as a Thought -> Code -> Observation cycle."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

from agent_smith.agent import observation
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
) -> SolutionOutput:
    """Run one task to a `SolutionOutput`. Never raises.

    A crash scores as an automatic fail, so every failure path returns a valid
    result with `success=False` and a populated `error` instead.

    The sandbox is driven here but built and disposed of by the caller: that is
    what lets a fake satisfy it, and it puts the `finally` that must survive a
    signal at the process entry point rather than inside a library.
    """
    run = _Run(task, clock)
    try:
        run.execute(provider, sandbox, max_iterations, compact)
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

    def execute(
        self,
        provider: LLMProvider,
        sandbox: Sandbox,
        max_iterations: int,
        compact: Callable[[list[Message]], list[Message]],
    ) -> None:
        """Turn the loop until an answer arrives or the iterations run out."""
        for step in range(1, max_iterations + 1):
            try:
                answer = provider.complete(compact(self.history))
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
                    self._record(step, answer, extracted.code, executed.stdout)
                    return
                said = observation.from_execution(
                    executed,
                    namespace_lost=sandbox.restarts != before,
                    repair_note=extracted.repair_note,
                )
                self._record(step, answer, extracted.code, said)
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

    def to_solution(self) -> SolutionOutput:
        """Everything the run accumulated, in the shape the moulinette reads."""
        return SolutionOutput(
            task_id=self._task.task_id,
            benchmark=self._task.benchmark,
            success=self.success,
            solution=self.solution,
            iterations=len(self.steps),
            total_requests=sum(1 + step.retries for step in self.steps),
            total_input_tokens=sum(step.input_tokens for step in self.steps),
            total_output_tokens=sum(step.output_tokens for step in self.steps),
            total_time_seconds=self._clock() - self._started,
            steps=self.steps,
            system_prompt=self._task.system_prompt,
            error=self.error,
        )
