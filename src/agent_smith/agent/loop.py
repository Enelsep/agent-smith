"""One task, run as a Thought -> Code -> Observation cycle."""

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
from agent_smith.sandbox.protocol import ExecResult, Outcome

if TYPE_CHECKING:
    from agent_smith.agent.task import TaskSpec
    from agent_smith.llm import LLMProvider

AnswerValidator = Callable[[str], str | None]
"""Judges a submission before the run is allowed to end on it.

Given what `final_answer` was called with, it returns `None` to accept or the
reason to refuse - addressed to the model, since that reason becomes the
observation it reads next. Judging the answer here rather than in the prompt is
what makes it hold whatever the model believes about its own work."""

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

    `validate_answer` is given the last say on a submission: a run ends on
    `final_answer` only if it returns `None`. Left out, any non-empty answer is
    taken as given, which is what every caller had before there was anything to
    check against.

    The transcript sent to the provider is compacted by default, via
    `compact_history` at its default `verbatim_steps`: older steps keep the
    code that ran and lose the reasoning that led there, so a long task does
    not pay to re-send its own past in full on every call. A caller with a
    different budget passes its own callable, such as
    `functools.partial(compact_history, verbatim_steps=N)`.
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
        # What the guard needs to convert its estimates into the billed
        # tokens the ceiling is denominated in, and to reserve the forced
        # call at the size the transcript will have reached by then. Both
        # are measured from the run itself rather than assumed.
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
            # A measured zero is an answer, not a missing one: a compaction
            # that holds the transcript flat means the forced call really
            # will be this size. Only the first iteration, which has nothing
            # to compare against, needs a stand-in — and reserving a second
            # copy of the view is the pessimistic one.
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
                # Added to the view, not to the transcript: the nudge is about
                # this call, and a forced turn may be followed by another one.
                # Keeping it out of the history means it is never read back as
                # part of the conversation, and it keeps `compact` to one call
                # per iteration — CORE-7 need not be idempotent, nor preserve
                # the last message it is handed.
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
            # Calibration, from the one pairing that is never a guess: what
            # this call was billed against what it was estimated at. Updated
            # only once a call has been billed, so a refusal cannot deflate it.
            self._worst_ratio = max(
                self._worst_ratio, billing_ratio(answer.input_tokens, estimated)
            )
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
                    rejection = (
                        None
                        if validate_answer is None
                        else validate_answer(executed.final_answer)
                    )
                    if rejection is None:
                        self.solution = executed.final_answer
                        self.success = True
                        self._record(
                            step,
                            answer,
                            extracted.code,
                            observation.combined_output(executed),
                        )
                        return
                    # A submission that does not survive its own tests is not
                    # an answer, and the run has budget left to say so. The
                    # reason takes the place of the observation, which is what
                    # gives the next turn something to act on.
                    said = rejection
                    if sandbox.restarts != before:
                        # Validating can cost the worker, and the model would
                        # otherwise keep referring to a namespace it has lost.
                        said = f"{said}\n\n{observation.NAMESPACE_LOST}"
                else:
                    said = observation.from_execution(
                        executed,
                        namespace_lost=sandbox.restarts != before,
                        repair_note=extracted.repair_note,
                    )
                self._record(step, answer, extracted.code, said)
            if reason == "wall_clock":
                # The one budget that cannot be reserved against. `can_afford_
                # forced_call` and `can_attempt_submission` let a token stop be
                # retried because both are measurable before the call; how long
                # a request will take is not knowable until it returns, so a
                # second attempt here is a bet against the only ceiling with no
                # way to check it first.
                self.error = (
                    f"stopped by the wall_clock budget guard at step {step}; "
                    "no final answer was received"
                )
                return
            # A forced turn that answered nothing usable is not the end of a
            # run stopped for tokens: the observation about to be appended is
            # what would fix the next attempt — a block that did not parse says
            # so — and the two affordability checks at the top of the next
            # iteration decide whether there is budget left to try again.
            # Returning here would discard both while the ceiling still had room.
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
