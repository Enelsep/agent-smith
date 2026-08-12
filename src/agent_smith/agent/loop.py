from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from agent_smith.agent import observation
from agent_smith.agent.budget import (
    FORCED_SUBMISSION_NUDGE,
    UNCALIBRATED_BILLING_RATIO,
    billing_ratio,
    can_afford_forced_call,
    can_attempt_submission,
    capped_max_tokens,
    estimate_tokens,
    output_reserve,
    remaining_output_tokens,
    should_force_submission,
)
from agent_smith.agent.history import compact_history
from agent_smith.extraction import extract_code
from agent_smith.llm import LLMResponse, Message, ProviderError
from agent_smith.models.contract import SolutionOutput, StepMetrics
from agent_smith.sandbox import feedback
from agent_smith.sandbox.protocol import ExecResult, Outcome

if TYPE_CHECKING:
    from agent_smith.agent.task import TaskSpec
    from agent_smith.llm import LLMProvider

AnswerValidator = Callable[[str], str | None]
"""Judges a submission before the run is allowed to end on it.
."""

UNCHANGED_ANSWER = (
    "final_answer refused: this is the submission that was just refused, "
    "unchanged, so it was not judged again — the answer cannot come out "
    "differently. Act on the reason above before submitting the same thing."
)


def judged_once(validate: AnswerValidator) -> AnswerValidator:
    """Wrap a validator so an unchanged resubmission is refused unjudged.
    Benchmark-agnostic, because the rule is about the answer and not about how
    it is judged: MBPP hands this an assertion run, SWE-bench a container one.
    """
    refused: str | None = None

    def once(submitted: str) -> str | None:
        nonlocal refused
        if submitted == refused:
            return UNCHANGED_ANSWER
        verdict = validate(submitted)
        refused = None if verdict is None else submitted
        return verdict

    return once


DEFAULT_MAX_ITERATIONS = 10

DEFAULT_MAX_INPUT_TOKENS = 6000
"""MBPP's cumulative input-token ceiling"""

DEFAULT_MAX_OUTPUT_TOKENS = 1500
"""MBPP's cumulative output-token ceiling (SWE-bench allows 10 000)."""

DEFAULT_MAX_WALL_CLOCK_SECONDS = 120.0
"""MBPP's wall-clock ceiling in seconds (SWE-bench allows 900)."""


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


def run_task(
    task: TaskSpec,
    provider: LLMProvider,
    sandbox: Sandbox,
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    compact: Callable[[list[Message]], list[Message]] = compact_history,
    clock: Callable[[], float] = time.monotonic,
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_wall_clock_seconds: float = DEFAULT_MAX_WALL_CLOCK_SECONDS,
    max_tokens_per_call: int | None = None,
    validate_answer: AnswerValidator | None = None,
) -> SolutionOutput:
    """Run one task to a `SolutionOutput`."""
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
            validate_answer,
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
        self._largest_growth: int | None = None
        self._last_estimate: int | None = None
        self._worst_ratio = 0.0

    @property
    def ratio(self) -> float:
        """Billed tokens per estimated one, at the worst rate seen so far.

        The largest observation rather than the average, because a ratio
        that rises during the run would otherwise be met with a figure
        that lags it, and lagging here means under-reserving. Falls back
        to a pessimistic guess until a call has actually been billed;
        `billing_ratio` floors every measurement at 1.0, so no real one
        can be mistaken for that absent state.
        """
        return self._worst_ratio or UNCALIBRATED_BILLING_RATIO

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
        validate_answer: AnswerValidator | None = None,
    ) -> None:
        """Turn the loop until an answer arrives, the budget runs out, or the
        iterations run out."""
        for step in range(1, max_iterations + 1):
            remaining_output = remaining_output_tokens(
                self.total_output_tokens, max_output_tokens
            )
            view = compact(self.history)
            estimated = estimate_tokens(view)
            if self._last_estimate is not None:
                self._largest_growth = max(
                    self._largest_growth or 0, estimated - self._last_estimate
                )
            self._last_estimate = estimated
            growth = estimated if self._largest_growth is None else self._largest_growth
            reason = should_force_submission(
                total_input_tokens=self.total_input_tokens,
                estimated_next_input=estimated,
                estimated_growth=growth,
                ratio=self.ratio,
                max_input_tokens=max_input_tokens,
                elapsed_seconds=self._clock() - self._started,
                max_wall_clock_seconds=max_wall_clock_seconds,
                remaining_output_tokens=remaining_output,
                reserved_output_tokens=output_reserve(max_output_tokens),
            )
            if reason is not None:
                if not can_afford_forced_call(
                    total_input_tokens=self.total_input_tokens,
                    estimated_next_input=estimated,
                    ratio=self.ratio,
                    max_input_tokens=max_input_tokens,
                ):
                    self.error = (
                        f"stopped by the input_tokens budget guard before "
                        f"step {step}: a final attempt would not fit under "
                        f"the {max_input_tokens}-token ceiling"
                    )
                    return
                if not can_attempt_submission(remaining_output):
                    self.error = (
                        f"stopped by the output_tokens budget guard before "
                        f"step {step}: {remaining_output} output tokens "
                        "remained, too few to attempt a final answer"
                    )
                    return
                view = [*view, {"role": "user", "content": FORCED_SUBMISSION_NUDGE}]
            try:
                answer = provider.complete(
                    view,
                    max_tokens=capped_max_tokens(max_tokens_per_call, remaining_output),
                )
            except ProviderError as refused:
                self.error = str(refused)
                return
            self._worst_ratio = max(
                self._worst_ratio, billing_ratio(answer.input_tokens, estimated)
            )
            self.history.append({"role": "assistant", "content": answer.text})
            extracted = extract_code(answer.text, step=step)
            if extracted.code is None:
                said = observation.from_extraction(extracted)
                self._record(step, answer, sandbox_input="", sandbox_output="")
            else:
                before = sandbox.restarts
                executed = sandbox.execute(extracted.code)
                if executed.outcome is Outcome.FINAL_ANSWER and executed.final_answer:
                    self.solution = executed.final_answer
                    rejection = (
                        None
                        if validate_answer is None
                        else validate_answer(executed.final_answer)
                    )
                    if rejection is None:
                        self.success = True
                        self._record(
                            step,
                            answer,
                            extracted.code,
                            observation.combined_output(executed),
                        )
                        return
                    said = rejection
                    if sandbox.restarts != before:
                        said = f"{said}\n\n{observation.NAMESPACE_LOST}"
                else:
                    said = observation.from_execution(
                        executed,
                        namespace_lost=sandbox.restarts != before,
                        repair_note=extracted.repair_note,
                    )
                self._record(step, answer, extracted.code, said)
            if answer.cut_short:
                said = f"{said}\n\n{feedback.reply_cut_short()}"
            if reason == "wall_clock":
                self.error = (
                    f"stopped by the wall_clock budget guard at step {step}; "
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
