"""One task, run as a Thought -> Code -> Observation cycle."""

import math
from collections.abc import Sequence

import pytest

from agent_smith.agent import budget, observation
from agent_smith.agent.history import TRUNCATION_MARKER
from agent_smith.agent.loop import run_task
from agent_smith.agent.task import TaskSpec
from agent_smith.llm import LLMResponse, Message, ProviderError
from agent_smith.models.contract import SolutionOutput
from agent_smith.sandbox.protocol import ExecResult, Outcome


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def a_task(**overrides: str) -> TaskSpec:
    fields = {
        "task_id": "11",
        "benchmark": "mbpp",
        "system_prompt": "You are a careful Python programmer.",
        "task_prompt": "Write a function that adds two numbers.",
    }
    fields.update(overrides)
    return TaskSpec(**fields)


def a_response(
    text: str = "```python\nprint(1)\n```", **overrides: object
) -> LLMResponse:
    fields: dict[str, object] = {
        "text": text,
        "input_tokens": 10,
        "output_tokens": 5,
        "latency_ms": 12.0,
        "model": "qwen",
        "api_url": "https://example.invalid/v1/chat/completions",
    }
    fields.update(overrides)
    return LLMResponse.model_validate(fields)


def ok(stdout: str = "") -> ExecResult:
    return ExecResult(outcome=Outcome.OK, stdout=stdout)


def answered(value: str | None) -> ExecResult:
    return ExecResult(outcome=Outcome.FINAL_ANSWER, final_answer=value)


class FakeProvider:
    """Answers from a script. A BaseException in the script is raised instead."""

    def __init__(self, script: Sequence[object]) -> None:
        self._script = list(script)
        self.calls: list[list[Message]] = []
        self.max_tokens_calls: list[int | None] = []

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.max_tokens_calls.append(max_tokens)
        answer = self._script.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, LLMResponse)
        return answer


class FakeSandbox:
    """Executes from a script, recording the code it was given.

    `restarts_before` names the 1-indexed calls that find a dead worker, which
    is how a silent restart is simulated without a real process.
    """

    def __init__(
        self,
        script: Sequence[ExecResult],
        *,
        restarts_before: Sequence[int] = (),
    ) -> None:
        self._script = list(script)
        self._restarts_before = set(restarts_before)
        self.received: list[str] = []
        self.restarts = 0

    def execute(self, code: str) -> ExecResult:
        self.received.append(code)
        if len(self.received) in self._restarts_before:
            self.restarts += 1
        return self._script.pop(0)


def test_a_task_solved_on_the_first_try() -> None:
    clock = FakeClock()
    provider = FakeProvider([a_response()])
    sandbox = FakeSandbox([answered("def add(a, b): return a + b")])

    solution = run_task(a_task(), provider, sandbox, clock=clock)

    assert solution.success is True
    assert solution.solution == "def add(a, b): return a + b"
    assert solution.iterations == 1
    assert solution.task_id == "11"
    assert solution.benchmark == "mbpp"
    assert solution.error is None


def test_the_first_call_carries_the_system_prompt_then_the_task() -> None:
    provider = FakeProvider([a_response()])
    sandbox = FakeSandbox([answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert provider.calls[0] == [
        {"role": "system", "content": "You are a careful Python programmer."},
        {"role": "user", "content": "Write a function that adds two numbers."},
    ]


def test_the_extracted_code_is_what_reaches_the_sandbox() -> None:
    provider = FakeProvider([a_response("```python\nprint(1)\n```")])
    sandbox = FakeSandbox([answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert sandbox.received == ["print(1)"]


def test_one_step_is_recorded_from_the_response() -> None:
    provider = FakeProvider([a_response(input_tokens=31, output_tokens=7, retries=2)])
    sandbox = FakeSandbox([answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    step = solution.steps[0]
    assert step.step == 1
    assert step.input_tokens == 31
    assert step.output_tokens == 7
    assert step.request_time_ms == 12.0
    assert step.model_name == "qwen"
    assert step.retries == 2
    assert step.llm_output == "```python\nprint(1)\n```"
    assert step.sandbox_input == "print(1)"


def test_the_winning_step_records_stderr_alongside_stdout() -> None:
    # Code that warns on stderr before calling final_answer() must not lose
    # that warning: sandbox_output combines both streams, matching how
    # observation._body renders a non-winning step.
    provider = FakeProvider([a_response()])
    sandbox = FakeSandbox(
        [
            ExecResult(
                outcome=Outcome.FINAL_ANSWER, final_answer="done", stderr="warned\n"
            )
        ]
    )

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.steps[0].sandbox_output == "warned"


def test_the_endpoint_is_recorded_in_full() -> None:
    # The moulinette only prints this field, so the full endpoint CORE-1
    # produced is kept rather than truncated to a base URL.
    provider = FakeProvider([a_response()])
    sandbox = FakeSandbox([answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.steps[0].api_url == "https://example.invalid/v1/chat/completions"


def test_the_system_prompt_is_reported_for_provenance() -> None:
    provider = FakeProvider([a_response()])
    sandbox = FakeSandbox([answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.system_prompt == "You are a careful Python programmer."


def test_the_run_is_timed_by_the_injected_clock() -> None:
    clock = FakeClock()

    class TimingProvider(FakeProvider):
        def complete(
            self,
            messages: Sequence[Message],
            stop: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> LLMResponse:
            clock.advance(1.5)
            return super().complete(messages, stop, max_tokens)

    provider = TimingProvider([a_response()])
    sandbox = FakeSandbox([answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=clock)

    assert solution.total_time_seconds == 1.5


def failed_extraction() -> LLMResponse:
    """A reply with no code block at all, so CORE-3 finds nothing to run."""
    return a_response("I think the answer is probably 42, but I am not sure.")


def test_a_second_attempt_sees_the_first_observation() -> None:
    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([ok("41\n"), answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert provider.calls[1] == [
        {"role": "system", "content": "You are a careful Python programmer."},
        {"role": "user", "content": "Write a function that adds two numbers."},
        {"role": "assistant", "content": "```python\nprint(1)\n```"},
        {"role": "user", "content": "41"},
    ]


def test_the_transcript_grows_by_two_messages_per_iteration() -> None:
    provider = FakeProvider([a_response(), a_response(), a_response()])
    sandbox = FakeSandbox([ok("a\n"), ok("b\n"), answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert [len(call) for call in provider.calls] == [2, 4, 6]


def test_a_reply_with_no_code_never_reaches_the_sandbox() -> None:
    provider = FakeProvider([failed_extraction(), a_response()])
    sandbox = FakeSandbox([answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert len(sandbox.received) == 1
    assert sandbox.received[0] == "print(1)"
    assert solution.steps[0].sandbox_input == ""
    assert solution.steps[0].sandbox_output == ""


def test_a_reply_with_no_code_still_tells_the_model_what_went_wrong() -> None:
    provider = FakeProvider([failed_extraction(), a_response()])
    sandbox = FakeSandbox([answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    said = provider.calls[1][-1]["content"]
    assert said != ""
    assert provider.calls[1][-1]["role"] == "user"


def test_a_silent_restart_warns_that_the_namespace_is_gone() -> None:
    # The sandbox restarts on its own when it finds a dead worker between
    # calls, and that path can still answer OK. Comparing `restarts` catches
    # it; reading the outcome would not.
    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([ok("fine\n"), answered("done")], restarts_before=[1])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert observation.NAMESPACE_LOST in provider.calls[1][-1]["content"]


def test_a_step_without_a_restart_says_nothing_about_the_namespace() -> None:
    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([ok("fine\n"), answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert observation.NAMESPACE_LOST not in provider.calls[1][-1]["content"]


@pytest.mark.parametrize(
    "executed",
    [
        pytest.param(
            ExecResult(
                outcome=Outcome.ERROR,
                error="ZeroDivisionError: division by zero",
            ),
            id="error",
        ),
        pytest.param(
            ExecResult(
                outcome=Outcome.SOFT_TIMEOUT,
                error="Execution exceeded the sandbox time limit",
            ),
            id="soft_timeout",
        ),
        pytest.param(
            # Built by the parent process, not the worker: stdout/stderr stay
            # empty, unlike ERROR and SOFT_TIMEOUT which come from the worker.
            ExecResult(
                outcome=Outcome.HARD_TIMEOUT,
                error="code did not return control after 35s and could not "
                "be interrupted",
            ),
            id="hard_timeout",
        ),
        pytest.param(
            ExecResult(
                outcome=Outcome.CRASHED,
                error="the sandbox worker died mid-execution",
            ),
            id="crashed",
        ),
        pytest.param(
            ExecResult(
                outcome=Outcome.SHUTDOWN,
                error="KeyboardInterrupt: ",
            ),
            id="shutdown",
        ),
    ],
)
def test_a_non_terminal_outcome_still_reaches_a_final_answer(
    executed: ExecResult,
) -> None:
    # None of these outcomes end the run: they are rendered as an observation
    # like any other, and the loop turns to a second iteration exactly as it
    # would after Outcome.OK.
    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([executed, answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.success is True
    assert solution.solution == "done"
    assert solution.iterations == 2
    assert solution.error is None


def test_compact_shapes_what_is_sent_without_touching_what_is_recorded() -> None:
    seen: list[list[Message]] = []

    def only_the_last(messages: list[Message]) -> list[Message]:
        seen.append(list(messages))
        return messages[-1:]

    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([ok("41\n"), answered("done")])

    solution = run_task(
        a_task(), provider, sandbox, compact=only_the_last, clock=FakeClock()
    )

    assert [len(call) for call in provider.calls] == [1, 1]
    # The second call is what proves the transcript itself was never touched:
    # it still carries all four messages, not the one-message view compact
    # handed to the provider on the call before.
    assert seen[1] == [
        {"role": "system", "content": "You are a careful Python programmer."},
        {"role": "user", "content": "Write a function that adds two numbers."},
        {"role": "assistant", "content": "```python\nprint(1)\n```"},
        {"role": "user", "content": "41"},
    ]
    # The full reply is still reported, because compaction shaped the view and
    # not the transcript.
    assert solution.steps[0].llm_output == "```python\nprint(1)\n```"


def test_running_out_of_iterations_fails_without_raising() -> None:
    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([ok("a\n"), ok("b\n")])

    solution = run_task(
        a_task(), provider, sandbox, max_iterations=2, clock=FakeClock()
    )

    assert solution.success is False
    assert solution.iterations == 2
    assert solution.solution == ""
    assert solution.error is not None
    assert "2 iterations" in solution.error


def test_a_provider_failure_ends_the_run_with_its_message_intact() -> None:
    # CORE-2 has already spent three attempts, key rotation and its budget.
    # The message is kept verbatim so CORE-7 can see which case to catch.
    provider = FakeProvider(
        [a_response(), ProviderError("endpoint answered 413", status_code=413)]
    )
    sandbox = FakeSandbox([ok("a\n")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.success is False
    assert solution.error == "endpoint answered 413"
    assert solution.iterations == 1


def test_a_provider_failure_on_the_first_call_still_returns_a_result() -> None:
    provider = FakeProvider([ProviderError("all 3 API keys are rate limited")])
    sandbox = FakeSandbox([])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.success is False
    assert solution.iterations == 0
    assert solution.total_requests == 0
    assert solution.error == "all 3 API keys are rate limited"


def test_final_answer_with_nothing_asks_again_rather_than_submitting_empty() -> None:
    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([answered(None), answered("the real answer")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.success is True
    assert solution.solution == "the real answer"
    assert solution.iterations == 2
    assert observation.EMPTY_ANSWER in provider.calls[1][-1]["content"]


def test_final_answer_with_an_empty_string_is_treated_the_same() -> None:
    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([answered(""), answered("the real answer")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.solution == "the real answer"
    assert solution.iterations == 2


def test_the_totals_are_sums_over_the_steps() -> None:
    provider = FakeProvider(
        [
            a_response(input_tokens=100, output_tokens=10),
            a_response(input_tokens=250, output_tokens=20),
        ]
    )
    sandbox = FakeSandbox([ok("a\n"), answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.total_input_tokens == 350
    assert solution.total_output_tokens == 30
    assert solution.iterations == len(solution.steps) == 2


def test_total_requests_counts_the_retries_core2_spent() -> None:
    # The field asks for real API requests, and CORE-2 may have made several
    # per step. One call with two retries is three requests.
    provider = FakeProvider([a_response(retries=2), a_response(retries=0)])
    sandbox = FakeSandbox([ok("a\n"), answered("done")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.total_requests == 4


class ExplodingSandbox:
    """A sandbox whose worker cannot be reached, the way `Sandbox` reports it."""

    restarts = 0

    def execute(self, code: str) -> ExecResult:
        raise RuntimeError("sandbox has no live connection to its worker")


class InterruptingSandbox:
    restarts = 0

    def execute(self, code: str) -> ExecResult:
        raise KeyboardInterrupt


def test_an_unexpected_failure_becomes_a_failed_result_not_a_traceback() -> None:
    provider = FakeProvider([a_response()])

    solution = run_task(a_task(), provider, ExplodingSandbox(), clock=FakeClock())

    assert solution.success is False
    assert solution.error is not None
    assert "no live connection" in solution.error
    assert solution.task_id == "11"


def test_what_the_run_had_done_survives_an_unexpected_failure() -> None:
    provider = FakeProvider([a_response(input_tokens=99), a_response()])

    class FailsOnTheSecondCall:
        restarts = 0

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, code: str) -> ExecResult:
            self.calls += 1
            if self.calls == 1:
                return ok("first\n")
            raise RuntimeError("the worker died")

    solution = run_task(a_task(), provider, FailsOnTheSecondCall(), clock=FakeClock())

    assert solution.success is False
    assert solution.iterations == 1
    assert solution.total_input_tokens == 99


def test_a_keyboard_interrupt_still_reaches_the_caller() -> None:
    # The counter-test for the boundary: catching BaseException instead of
    # Exception would swallow this, and nothing else would notice.
    provider = FakeProvider([a_response()])

    with pytest.raises(KeyboardInterrupt):
        run_task(a_task(), provider, InterruptingSandbox(), clock=FakeClock())


def test_a_system_exit_still_reaches_the_caller() -> None:
    class ExitingSandbox:
        restarts = 0

        def execute(self, code: str) -> ExecResult:
            raise SystemExit(1)

    provider = FakeProvider([a_response()])

    with pytest.raises(SystemExit):
        run_task(a_task(), provider, ExitingSandbox(), clock=FakeClock())


def test_the_wall_clock_budget_forces_one_last_submission_attempt() -> None:
    clock = FakeClock()

    class TimingProvider(FakeProvider):
        def complete(
            self,
            messages: Sequence[Message],
            stop: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> LLMResponse:
            clock.advance(110.0)
            return super().complete(messages, stop, max_tokens)

    provider = TimingProvider([a_response(), a_response()])
    sandbox = FakeSandbox([ok("not done yet\n"), ok("still not done\n")])

    solution = run_task(
        a_task(),
        provider,
        sandbox,
        clock=clock,
        max_wall_clock_seconds=120.0,
    )

    assert solution.success is False
    assert solution.error is not None
    assert "wall_clock" in solution.error
    assert solution.iterations == 2
    assert provider.calls[1][-1] == {
        "role": "user",
        "content": budget.FORCED_SUBMISSION_NUDGE,
    }


def test_the_input_token_budget_forces_one_last_submission_attempt() -> None:
    provider = BillingProvider()
    sandbox = FakeSandbox([a_bulky_observation(), ok("still not done\n")])

    solution = run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        max_input_tokens=FORCING_INPUT_CEILING,
    )

    assert solution.success is False
    assert solution.error is not None
    assert "input_tokens" in solution.error
    assert solution.iterations == 2
    assert provider.calls[1][-1] == {
        "role": "user",
        "content": budget.FORCED_SUBMISSION_NUDGE,
    }


def test_a_forced_submission_attempt_can_still_succeed() -> None:
    provider = BillingProvider()
    sandbox = FakeSandbox([a_bulky_observation(), answered("done")])

    solution = run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        max_input_tokens=FORCING_INPUT_CEILING,
    )

    assert solution.success is True
    assert solution.solution == "done"
    assert solution.error is None
    assert provider.calls[1][-1] == {
        "role": "user",
        "content": budget.FORCED_SUBMISSION_NUDGE,
    }


class BillingProvider:
    """Bills for the prompt it was actually sent, times `ratio`.

    `FakeProvider` reads its token counts off a script, so a test built on
    it can assert what the guard *said* but never what the run *spent* —
    the numbers are unrelated to the transcript that produced them. Worse,
    the guard now calibrates billed tokens against its own estimate, and a
    scripted 5 200 against a 19-token prompt reads as an endpoint billing
    270× over, which it rightly refuses to keep spending against. Any test
    that needs the input guard to behave realistically needs this double.

    `ratio` above 1 stands for the endpoint billing more than chars/4
    predicts, which is the normal case for code. `script` supplies reply
    texts, or exceptions to raise, in place of the default reply; billing
    stays coupled to the prompt either way.
    """

    def __init__(
        self,
        ratio: float = 1.0,
        reply_chars: int = 800,
        script: Sequence[object] | None = None,
    ) -> None:
        self._ratio = ratio
        self._reply_chars = reply_chars
        self._script = None if script is None else list(script)
        self.calls: list[list[Message]] = []
        self.max_tokens_calls: list[int | None] = []

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.max_tokens_calls.append(max_tokens)
        # Rounded up: a double that under-bills would make every ceiling
        # test optimistic and hide an off-by-one crossing at the boundary.
        billed = math.ceil(self._ratio * budget.estimate_tokens(messages))
        text = "```python\nx = 1  # " + "a" * self._reply_chars + "\n```"
        if self._script is not None:
            nxt = self._script.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            assert isinstance(nxt, str)
            text = nxt
        return a_response(text, input_tokens=billed, output_tokens=20)


# A transcript and ceiling sized so the guard forces on the second call:
# the first fits with room reserved for one more, the second does not.
#
# The observation is deliberately substantial. With a one-line one the
# transcript grows so slowly that the window between "forces at step 1"
# and "does not force until step 3" is about 17 tokens wide, and any
# reprompting would land outside it. At this size the window is 1825-2200,
# and the ceiling below sits near its centre rather than against an edge,
# so CORE-6 rewording a prompt does not quietly move these tests onto a
# different branch.
FORCING_SYSTEM_PROMPT = "S" * 1200
FORCING_INPUT_CEILING = 2000


def forcing_task() -> TaskSpec:
    return a_task(system_prompt=FORCING_SYSTEM_PROMPT)


def a_bulky_observation() -> ExecResult:
    return ok("o" * 400 + "\n")


@pytest.mark.parametrize("ratio", [1.0, 1.33, 1.6, 2.5])
def test_a_forced_run_finishes_under_the_input_ceiling(ratio: float) -> None:
    # The property the guard exists for. It holds because the forced call
    # is reserved before the one in front of it is authorised, and both are
    # priced in billed tokens rather than estimated ones.
    #
    # 1.0 is an endpoint billing exactly what chars/4 predicts, 1.33 is
    # real code at 3 chars per token, 1.6 is 2.5, and 2.5 is 1.6 — denser
    # than these benchmarks produce. The divisor being wrong does not
    # matter once a call has been billed: the ratio is measured, so the
    # guard holds across the range rather than up to some limit.
    provider = BillingProvider(ratio=ratio)
    sandbox = FakeSandbox([ok("o" * 400 + "\n")] * 30)

    solution = run_task(
        a_task(system_prompt="S" * 1200, task_prompt="T" * 200),
        provider,
        sandbox,
        clock=FakeClock(),
        max_iterations=30,
        max_input_tokens=6000,
        max_output_tokens=100000,
    )

    assert solution.error is not None
    assert "input_tokens" in solution.error
    assert solution.total_input_tokens <= 6000


def test_an_exhausted_output_budget_never_reaches_the_provider() -> None:
    # A step can drain the budget from above the reserve to near zero in
    # one completion. What is left cannot carry a final_answer(), so the
    # run ends rather than spending a request on it.
    provider = FakeProvider([a_response(output_tokens=1495), a_response()])
    sandbox = FakeSandbox([ok("not done yet\n")])

    solution = run_task(
        a_task(), provider, sandbox, clock=FakeClock(), max_output_tokens=1500
    )

    assert provider.max_tokens_calls == [1500]
    assert solution.success is False
    assert solution.error is not None
    assert "output_tokens" in solution.error


def test_a_forced_call_that_cannot_fit_is_never_made() -> None:
    # The input backstop. A first prompt this large against this ceiling
    # leaves no room for even one request, and a run that crosses the
    # ceiling is scored a failure whatever it answers — so the request is
    # not spent. Nothing is billed and nothing is recorded.
    provider = BillingProvider()
    sandbox = FakeSandbox([])

    solution = run_task(
        a_task(system_prompt="S" * 20000),
        provider,
        sandbox,
        clock=FakeClock(),
        max_input_tokens=6000,
    )

    assert provider.calls == []
    assert solution.iterations == 0
    assert solution.total_requests == 0
    assert solution.total_input_tokens == 0
    assert solution.success is False
    assert solution.error is not None
    assert "input_tokens" in solution.error
    assert "6000" in solution.error


def test_a_ceiling_below_the_viable_floor_spends_no_request_at_all() -> None:
    provider = FakeProvider([a_response()])
    sandbox = FakeSandbox([])

    solution = run_task(
        a_task(), provider, sandbox, clock=FakeClock(), max_output_tokens=10
    )

    assert provider.max_tokens_calls == []
    assert solution.iterations == 0
    assert solution.total_requests == 0
    assert solution.error is not None
    assert "output_tokens" in solution.error


def test_an_unanswerable_attempt_names_the_budget_that_blocked_it() -> None:
    # The wall clock trips first, but what makes the attempt impossible is
    # the output budget, and that is what the error has to name for a
    # failure-category breakdown to be worth anything.
    clock = FakeClock()

    class TimingProvider(FakeProvider):
        def complete(
            self,
            messages: Sequence[Message],
            stop: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> LLMResponse:
            clock.advance(110.0)
            return super().complete(messages, stop, max_tokens)

    provider = TimingProvider([a_response(output_tokens=1500), a_response()])
    sandbox = FakeSandbox([ok("not done yet\n")])

    solution = run_task(
        a_task(),
        provider,
        sandbox,
        clock=clock,
        max_wall_clock_seconds=120.0,
        max_output_tokens=1500,
    )

    assert provider.max_tokens_calls == [1500]
    assert solution.error is not None
    assert "output_tokens" in solution.error
    assert "wall_clock" not in solution.error


def test_a_provider_failure_on_the_forced_call_keeps_its_message() -> None:
    provider = BillingProvider(
        script=[
            "```python\nx = 1  # " + "a" * 800 + "\n```",
            ProviderError("endpoint answered 503"),
        ]
    )
    sandbox = FakeSandbox([a_bulky_observation()])

    solution = run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        max_input_tokens=FORCING_INPUT_CEILING,
    )

    assert solution.success is False
    assert solution.error == "endpoint answered 503"


def test_the_nudge_stays_out_of_the_recorded_transcript() -> None:
    # It goes into the view the provider is handed, never into the
    # transcript: nothing reads it back, and keeping it out is what frees
    # CORE-7 from having to preserve the last message it returns.
    seen: list[list[Message]] = []

    def watching(messages: list[Message]) -> list[Message]:
        seen.append(list(messages))
        return messages

    provider = BillingProvider()
    sandbox = FakeSandbox([a_bulky_observation(), answered("done")])

    run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        compact=watching,
        max_input_tokens=FORCING_INPUT_CEILING,
    )

    assert provider.calls[1][-1]["content"] == budget.FORCED_SUBMISSION_NUDGE
    assert all(
        message["content"] != budget.FORCED_SUBMISSION_NUDGE
        for transcript in seen
        for message in transcript
    )


def test_the_output_reserve_still_leaves_room_to_answer() -> None:
    # Below the reserve but above the viable floor: the forced attempt is
    # made, and it gets the tokens the reserve held back.
    provider = FakeProvider([a_response(output_tokens=1250), a_response()])
    sandbox = FakeSandbox([ok("not done yet\n"), answered("done")])

    solution = run_task(
        a_task(), provider, sandbox, clock=FakeClock(), max_output_tokens=1500
    )

    assert provider.max_tokens_calls == [1500, 250]
    assert solution.success is True


def test_a_forced_iteration_ends_the_run_once_nothing_more_fits() -> None:
    # A forced turn that answers nothing is retried while the ceiling has room
    # for another attempt. This ceiling has none, so the run ends here — and it
    # ends on the affordability check rather than on a rule that the forced
    # turn is always the last one.
    provider = BillingProvider(
        script=[
            "```python\nx = 1  # " + "a" * 800 + "\n```",
            "I think the answer is probably 42, but I am not sure.",
        ]
    )
    sandbox = FakeSandbox([a_bulky_observation()])

    solution = run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        max_input_tokens=EXHAUSTED_INPUT_CEILING,
    )

    assert solution.success is False
    assert solution.iterations == 2
    assert solution.error is not None
    assert "input_tokens" in solution.error
    assert "would not fit" in solution.error
    assert solution.steps[1].sandbox_input == ""


def test_the_configured_per_call_ceiling_is_never_exceeded() -> None:
    # ResolvedConfig.max_tokens, from models.json. The loop lowers it as
    # the cumulative budget drains but must never raise it.
    provider = FakeProvider(
        [
            a_response(output_tokens=100),
            a_response(output_tokens=100),
            a_response(),
        ]
    )
    sandbox = FakeSandbox([ok("a\n"), ok("b\n"), answered("done")])

    run_task(
        a_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        max_output_tokens=1500,
        max_tokens_per_call=400,
    )

    assert provider.max_tokens_calls == [400, 400, 400]


def test_the_nudge_survives_a_compaction_that_drops_the_tail() -> None:
    # CORE-7 may summarise the transcript into a head window, or into a
    # single message. The nudge is added to the view after compaction, so
    # a forced attempt stays forced whatever compaction does.
    #
    # Forced on the wall clock rather than on tokens, because a compaction
    # this aggressive holds the transcript at a constant size — which is
    # precisely why the input guard never trips under it.
    clock = FakeClock()

    class TimingBillingProvider(BillingProvider):
        def complete(
            self,
            messages: Sequence[Message],
            stop: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> LLMResponse:
            clock.advance(110.0)
            return super().complete(messages, stop, max_tokens)

    provider = TimingBillingProvider()
    sandbox = FakeSandbox([a_bulky_observation(), answered("done")])

    run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=clock,
        compact=lambda messages: messages[:1],
        max_wall_clock_seconds=120.0,
    )

    assert provider.calls[1][-1] == {
        "role": "user",
        "content": budget.FORCED_SUBMISSION_NUDGE,
    }


def test_a_compaction_that_holds_the_view_flat_is_not_forced_early() -> None:
    # A growth measured as zero is an answer, not a missing one. Treating
    # it as unknown reserves a second copy of the view for a forced call
    # that will be exactly this size, and spends iterations the run could
    # have used to solve. Under CORE-7 a flat transcript is the normal
    # case, not an edge one — that is what compaction is for.
    #
    # At this ceiling the distinction is worth exactly one iteration:
    # passing the measured zero through reaches all ten, substituting the
    # view size for it stops at nine.
    provider = BillingProvider()
    sandbox = FakeSandbox([ok("o" * 400 + "\n")] * 12)

    solution = run_task(
        a_task(system_prompt="S" * 1200),
        provider,
        sandbox,
        clock=FakeClock(),
        max_iterations=10,
        compact=lambda messages: messages[:2],
        max_input_tokens=4000,
        max_output_tokens=10**6,
    )

    assert solution.iterations == 10
    assert solution.total_input_tokens <= 4000


def test_compaction_runs_once_per_iteration_even_when_forced() -> None:
    # CORE-7 is free to be expensive — it may summarise with an LLM call.
    # Estimating the request must not cost it a second invocation.
    seen: list[int] = []

    def counting(messages: list[Message]) -> list[Message]:
        seen.append(len(messages))
        return messages

    provider = BillingProvider()
    sandbox = FakeSandbox([a_bulky_observation(), answered("done")])

    run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        compact=counting,
        max_input_tokens=FORCING_INPUT_CEILING,
    )

    # Asserting the count alone would keep passing if the trip point moved
    # and no iteration were ever forced.
    assert provider.calls[1][-1]["content"] == budget.FORCED_SUBMISSION_NUDGE
    assert len(seen) == 2


def test_the_output_budget_shrinks_the_max_tokens_requested_each_call() -> None:
    provider = FakeProvider(
        [
            a_response(output_tokens=500),
            a_response(output_tokens=500),
            a_response(output_tokens=500),
        ]
    )
    sandbox = FakeSandbox([ok("a\n"), ok("b\n"), answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock(), max_output_tokens=1500)

    assert provider.max_tokens_calls == [1500, 1000, 500]


def test_the_result_of_a_real_run_satisfies_the_contract() -> None:
    # The wiring test: the real extractor, a scripted provider and sandbox.
    provider = FakeProvider(
        [a_response("Here you go:\n```python\nresult = 1 + 1\nprint(result)\n```")]
    )
    sandbox = FakeSandbox([answered("def add(a, b):\n    return a + b")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert sandbox.received == ["result = 1 + 1\nprint(result)"]
    assert SolutionOutput.model_validate_json(solution.model_dump_json()) == solution


def test_a_caller_who_says_nothing_still_gets_a_flat_transcript() -> None:
    # The reasoning behind DEFAULT_MAX_ITERATIONS applies to compaction too:
    # a caller who forgets must not be able to invalidate a run silently.
    provider = FakeProvider(
        [
            a_response("Thought: one\n```python\nprint(1)\n```"),
            a_response("Thought: two\n```python\nprint(2)\n```"),
            a_response("```python\nfinal_answer('done')\n```"),
        ]
    )
    sandbox = FakeSandbox([ok("1"), ok("2"), answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    third_call = provider.calls[2]
    assert TRUNCATION_MARKER in third_call[2]["content"]
    assert "Thought: one" not in third_call[2]["content"]
    assert "Thought: two" in third_call[4]["content"]


EXHAUSTED_INPUT_CEILING = 1800
"""A ceiling too tight for any attempt after the forced one, anywhere in 1700-1900."""

RETRYABLE_INPUT_CEILING = 2200
"""A ceiling with room for a second forced attempt after the first answers nothing.

`FORCING_INPUT_CEILING` deliberately has none, so it exercises the other branch.
Two attempts fit anywhere in 2120-2320 with this transcript; the middle is taken
so rewording a prompt cannot quietly move the test onto the one-attempt branch.
"""

MALFORMED_SUBMISSION = "```python\nfinal_answer '''(\ndef f():\n    pass\n)'''\n```"
"""A fenced block that does not parse, which is what task 260 actually sent."""


def test_a_forced_turn_that_answers_nothing_is_tried_again_while_budget_allows() -> (
    None
):
    # The forced turn is the model's last chance and it can waste it on a block
    # that does not parse: task 260 sent `final_answer \'\'\'(` and the run ended
    # with an empty solution. Stopping there throws away budget that was still
    # there, and throws away the one thing that would fix the next attempt --
    # the observation naming the syntax error.
    provider = BillingProvider(
        script=[
            "```python\nx = 1\n```",
            "```python\ny = 2\n```",
            MALFORMED_SUBMISSION,
            "```python\nfinal_answer('''def f():\n    pass\n''')\n```",
        ]
    )
    sandbox = FakeSandbox(
        [a_bulky_observation(), a_bulky_observation(), answered("done")]
    )

    result = run_task(
        forcing_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        max_input_tokens=RETRYABLE_INPUT_CEILING,
    )

    nudged = [
        i
        for i, call in enumerate(provider.calls)
        if call[-1]["content"] == budget.FORCED_SUBMISSION_NUDGE
    ]
    assert nudged == [2, 3], f"expected two forced attempts, got {nudged}"
    assert result.success is True
    assert result.solution == "done"


def test_a_submission_the_validator_rejects_does_not_end_the_run() -> None:
    # The prompt asks the model to run the assertions before submitting;
    # nothing makes it. Task 84 read `1 1` where the test wanted `1 2`, called
    # that a match and submitted. A harness that checks the answer itself holds
    # whatever the model believes.
    provider = FakeProvider(
        [
            a_response("```python\nfinal_answer('wrong')\n```"),
            a_response("```python\nfinal_answer('right')\n```"),
        ]
    )
    sandbox = FakeSandbox([answered("wrong"), answered("right")])
    rejected: list[str] = []

    def validate(submitted: str) -> str | None:
        rejected.append(submitted)
        return None if submitted == "right" else "it failed the given tests"

    solution = run_task(
        a_task(), provider, sandbox, clock=FakeClock(), validate_answer=validate
    )

    assert rejected == ["wrong", "right"]
    assert solution.success is True
    assert solution.solution == "right"
    assert solution.iterations == 2


def test_the_rejection_is_what_the_model_reads_next() -> None:
    # A rejection the model never sees is a wasted iteration: it would submit
    # the same answer again.
    provider = FakeProvider(
        [
            a_response("```python\nfinal_answer('wrong')\n```"),
            a_response("```python\nfinal_answer('right')\n```"),
        ]
    )
    sandbox = FakeSandbox([answered("wrong"), answered("right")])

    run_task(
        a_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        validate_answer=lambda s: None if s == "right" else "assert add(1, 2) failed",
    )

    second_call = provider.calls[1]
    assert "assert add(1, 2) failed" in second_call[-1]["content"]


def test_without_a_validator_a_submission_is_taken_as_given() -> None:
    # The default has to stay what every existing caller relies on.
    provider = FakeProvider([a_response("```python\nfinal_answer('whatever')\n```")])
    sandbox = FakeSandbox([answered("whatever")])

    solution = run_task(a_task(), provider, sandbox, clock=FakeClock())

    assert solution.success is True
    assert solution.solution == "whatever"


def test_a_rejected_submission_is_recorded_as_the_step_that_it_was() -> None:
    # The metrics must show the submission that was refused, not a gap.
    provider = FakeProvider(
        [
            a_response("```python\nfinal_answer('wrong')\n```"),
            a_response("```python\nfinal_answer('right')\n```"),
        ]
    )
    sandbox = FakeSandbox([answered("wrong"), answered("right")])

    solution = run_task(
        a_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        validate_answer=lambda s: None if s == "right" else "no",
    )

    assert solution.steps[0].sandbox_input == "final_answer('wrong')"
    assert solution.steps[0].sandbox_output == "no"


def test_a_run_that_never_satisfies_the_validator_still_answers() -> None:
    # The grader scores the string, not the flag. An attempt the validator
    # refused can still pass the tests it is actually judged on -- our sandbox
    # is not the container it will run in -- and an empty solution never can.
    provider = FakeProvider(
        [a_response("```python\nfinal_answer('best effort')\n```")] * 2
    )
    sandbox = FakeSandbox([answered("best effort")] * 2)

    solution = run_task(
        a_task(),
        provider,
        sandbox,
        clock=FakeClock(),
        max_iterations=2,
        validate_answer=lambda _: "it failed the given tests",
    )

    assert solution.success is False
    assert solution.solution == "best effort"
    assert solution.error is not None


def test_a_reply_cut_off_mid_comment_is_told_both_things() -> None:
    # The observed failure: the model plans in comments, the token cap stops it
    # before it writes any code, and the harness answers "no code block" -- true
    # but useless. What it can act on is that it was cut off, and that comments
    # are not what runs.
    provider = FakeProvider(
        [
            a_response("```python\n# first I need to handle the empty", cut_short=True),
            a_response("```python\nfinal_answer('done')\n```"),
        ]
    )
    sandbox = FakeSandbox([answered("done")])

    run_task(a_task(), provider, sandbox, clock=FakeClock())

    said = provider.calls[1][-1]["content"]
    assert "cut off at its token limit" in said
    assert "Comments do not run" in said
