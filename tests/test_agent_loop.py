"""One task, run as a Thought -> Code -> Observation cycle."""

from collections.abc import Sequence

from agent_smith.agent import observation
from agent_smith.agent.loop import run_task
from agent_smith.agent.task import TaskSpec
from agent_smith.llm import LLMResponse, Message, ProviderError
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

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
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


def test_compact_shapes_what_is_sent_without_touching_what_is_recorded() -> None:
    def only_the_last(messages: list[Message]) -> list[Message]:
        return messages[-1:]

    provider = FakeProvider([a_response(), a_response()])
    sandbox = FakeSandbox([ok("41\n"), answered("done")])

    solution = run_task(
        a_task(), provider, sandbox, compact=only_the_last, clock=FakeClock()
    )

    assert [len(call) for call in provider.calls] == [1, 1]
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
