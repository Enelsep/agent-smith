"""One task, run as a Thought -> Code -> Observation cycle."""

from collections.abc import Sequence

from agent_smith.agent.loop import run_task
from agent_smith.agent.task import TaskSpec
from agent_smith.llm import LLMResponse, Message
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
