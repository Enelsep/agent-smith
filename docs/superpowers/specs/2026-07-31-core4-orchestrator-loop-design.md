# CORE-4 · Orchestrator loop — design

The `Thought → Code → Observation` cycle: ask the model, extract what it wrote, run it, tell it what
happened, repeat until it submits an answer or the iterations run out.

## What this delivers

`run_task()` — one function that turns a task description into a `SolutionOutput`. It builds the
message list, calls the provider, extracts code with CORE-3, executes it in the SBX-1 sandbox,
appends the observation, records one `StepMetrics` per iteration, and stops on `final_answer()`.

It never raises. Every failure path returns a valid `SolutionOutput` with `success=false` and a
populated `error`, because a crash scores as an automatic fail.

## Scope boundary

CORE-4 blocks five cards, so every line drawn here becomes a contract someone else builds against.

| Card | Owns | Attaches through |
|---|---|---|
| CORE-5 Budget guard | token and wall-clock ceilings | new value parameters |
| CORE-6 Prompts | the content of both prompts | `TaskSpec` fields |
| CORE-7 Compaction | rewriting history before a call | the `compact` callable |
| SBX-7 Feedback | the shape of an observation | `observation.py`, rewritten in place |
| MBPP-1 / SWE-3 | the CLIs | they build `TaskSpec` and write the JSON |

What CORE-4 does **not** own:

- **Writing `solution.json`.** `run_task()` returns a `SolutionOutput`; the CLI writes it. Keeping
  the filesystem out is what lets every test of the loop run in memory. The `finally` that must
  survive a `KeyboardInterrupt` belongs at the process entry point, not inside a library — a
  library cannot promise anything about a signal delivered to its caller.
- **Constructing or disposing of the sandbox.** The loop drives it — calls `execute()`, reads
  `restarts` — but the CLI writes `with Sandbox(...) as sb`. Injection is what makes a fake sandbox
  possible, and disposal belongs with the same `finally` as the JSON.
- **Verifying the solution.** `success` means the agent called `final_answer()`, nothing more. The
  contract says "whether the agent *believes* it solved the task"; judging is the moulinette's job.

## One loop for both benchmarks

There is no MBPP branch and no SWE-bench branch. The frozen contracts already did the converging
work: `SolutionOutput.solution` is "the Python function code" for MBPP and "the git patch" for
SWE-bench, and in both cases it is **exactly the string the agent passed to `final_answer()`** —
`final_answer(code)` on one side, `final_answer(get_patch())` on the other. `ExecResult.final_answer`
is already a `str`.

So the loop treats `solution` as an opaque string and copies `task_id` and `benchmark` into the
result without interpreting either. What actually differs between the two benchmarks lives outside:
the prompt (CORE-6), the tools reachable from the sandbox (MCP-2/3/4), the container (SWE-1/2), and
the iteration ceiling.

A hook-based design was considered and rejected. No divergence can be named today that is not
settled either in the prompt or upstream of the loop, and inventing extension points for
divergences we cannot describe is how a loop acquires branches nobody ever takes.

## Module layout

```
src/agent_smith/agent/
    __init__.py       re-exports TaskSpec and run_task, nothing else
    task.py           TaskSpec
    observation.py    every outcome, rendered as the text the model reads
    loop.py           run_task() and the private accumulator behind it
```

`__init__.py` re-exports the two public names and no transport, on the pattern `llm/__init__.py`
set: importing the package must load neither `httpx` nor `multiprocessing`.

## `TaskSpec`

A frozen Pydantic model carrying `task_id`, `benchmark`, `system_prompt` and `task_prompt`.

Four bare `str` parameters would swap silently at a call site. A model names them, validates them,
and is the written contract between this card and the two CLIs that build one.

`system_prompt` is carried rather than derived because `SolutionOutput.system_prompt` requires it
verbatim for provenance checking.

## `run_task()`

```python
def run_task(
    task: TaskSpec,
    provider: LLMProvider,
    sandbox: Sandbox,
    *,
    max_iterations: int = 10,
    compact: Callable[[list[Message]], list[Message]] = _unchanged,
    clock: Callable[[], float] = time.monotonic,
) -> SolutionOutput: ...
```

`provider` is typed by the CORE-1 Protocol, so the loop is indifferent to whether CORE-2's retry
layer is wrapped around it. `sandbox` is the concrete SBX-1 class: there is no second implementation
in sight, and a scripted fake satisfies it structurally in tests.

`max_iterations` defaults to 10, the MBPP ceiling. SWE-bench allows 30 and its CLI passes it. The
stricter of the two is the default, so a caller that forgets cannot silently invalidate a run.

`clock` is injected because elapsed time is the loop's only measurement, and a test that asserts
`total_time_seconds` exactly is worth more than one that brackets it. `compact` is the CORE-7 seam
and defaults to identity.

### The boundary

The accumulating state lives outside the `try`, so a failure at iteration 7 still reports the first
six:

```python
run = _Run(task, clock)
try:
    run.execute(provider, sandbox, max_iterations, compact)
except Exception as unexpected:
    run.error = f"the agent loop failed: {unexpected}"
return run.to_solution()
```

`Exception`, never `BaseException`. `KeyboardInterrupt` and `SystemExit` must keep propagating —
the card requires it, and CORE-3 already set the pattern in `extract_code`. `FinalAnswerSignal`
subclasses `BaseException` but never crosses this boundary: it is raised and caught inside the
worker process and arrives here only as `Outcome.FINAL_ANSWER`.

The boundary is not defensive politeness. `sandbox.execute()` genuinely raises: `RuntimeError` when
a restart leaves no live connection, and `OSError` from `mp.Process.start()`. Our own bugs are the
third source, and they must produce a valid JSON rather than a traceback.

### The loop

```
history = [system(task.system_prompt), user(task.task_prompt)]

for step in 1..max_iterations:
    answer = provider.complete(compact(history))     # ProviderError ends the run
    history.append(assistant(answer.text))
    result = extract_code(answer.text, step=step)
    if result.code is None:
        observation = from_extraction(result)
    else:
        before = sandbox.restarts
        executed = sandbox.execute(result.code)          # an ExecResult
        if executed.outcome is FINAL_ANSWER and executed.final_answer:
            record the step; solution = executed.final_answer; success = True; stop
        observation = from_execution(executed, sandbox.restarts != before)
    history.append(user(observation))
    record the step
```

`complete()` is called with the messages alone. Stop sequences and `max_tokens` reach the provider
through `ResolvedConfig`, which CORE-1 already applies as its defaults, so the loop never names a
stop sequence and CORE-6 can change them per model without reopening this file.

`compact(history)` is applied to a *view* passed to the provider, never to `history` itself. The
loop keeps the full transcript because `StepMetrics.llm_output` must report it verbatim; CORE-7 can
summarise what it sends without destroying what it records.

The transcript is a flat alternating list — `system`, `user`, then `assistant`/`user` per step —
rather than one re-rendered `user` message per turn. CORE-7's compaction is specified as "keep
system + task + the first 2 steps + the last N", which on a flat list is three slices and a
concatenation. On a re-rendered transcript it would mean re-parsing text we had just formatted
ourselves to find the step boundaries again.

Two messages per iteration is what makes the input grow quadratically; the project plan already
projects 6 400 input tokens by iteration 4. That is not a flaw of this shape, it is what makes
CORE-5 and CORE-7 necessary, and the plan says so.

### What ends the run

| Condition | Result |
|---|---|
| `FINAL_ANSWER` with a non-empty value | `success=true`, `solution` is that value |
| `max_iterations` reached | `success=false`, `error` names the exhausted budget |
| `ProviderError` (including `AllKeysParked`) | `success=false`, `error` is the message verbatim |
| Anything unexpected | `success=false`, `error` names the failure |

Everything else becomes an observation and the loop continues: `OK`, `ERROR`, `SOFT_TIMEOUT`,
`HARD_TIMEOUT`, `CRASHED`, `SHUTDOWN`, an extraction that found no code, and `final_answer()` called
with nothing.

A `ProviderError` ends the run rather than being retried. CORE-2 has already spent its policy —
three attempts, key rotation, a 20 s budget — and stacking a second retry layer on top would
duplicate the policy and burn the task's wall clock. CORE-2's own docstring anticipates an
orchestrator that reacts to a 400 or 413 by *changing the prompt*; the useful reaction to an
over-long context is to compact the history, and compaction does not exist yet. Retrying with an
identical history would fail identically, more slowly. The error message is stored verbatim, HTTP
status included, so CORE-7 can see exactly which case it will have to catch.

`final_answer()` called with `None` or `""` returns an observation saying nothing was submitted,
rather than ending the run. Stopping with `success=true` and an empty solution guarantees a failed
task; letting the agent try again costs three lines and stays bounded by `max_iterations`.

### Losing the namespace

The loop reads `sandbox.restarts` before executing and compares afterwards, rather than inferring
loss from the `HARD_TIMEOUT` and `CRASHED` outcomes.

`Sandbox.execute()` restarts on its own when it finds a dead worker between calls, and that path can
return `OK` on the retry while every variable defined so far is gone. Comparing the counter catches
it; enumerating outcomes does not. When the counter moved, the observation tells the model its
variables are lost — otherwise it will keep referring to names that no longer exist.

## `observation.py`

One module, one job: turn an outcome into text addressed to the model.

CORE-3's `failure` strings are already written as messages to the model ("Could not read your
reply: …") and are passed through unchanged. When `repaired` is true the observation carries the
`repair_note`: a model that is never told its fence was unclosed will keep sending unclosed fences.

Its shape is deliberately not abstracted. SBX-7 owns structured feedback messages and belongs to
another stream; inventing its interface on its behalf would constrain it rather than help it. When
SBX-7 lands it rewrites this module, which is why the module exists separately at all.

## Filling the contract

Per step:

| `StepMetrics` field | Source |
|---|---|
| `step` | 1-indexed iteration |
| `input_tokens`, `output_tokens` | `LLMResponse` |
| `request_time_ms` | `LLMResponse.latency_ms` |
| `model_name` | `LLMResponse.model` |
| `retries` | `LLMResponse.retries` — the feedback loop closed with CORE-2 |
| `llm_output` | `answer.text`, verbatim |
| `sandbox_input` | the extracted code, `""` when extraction found none |
| `sandbox_output` | the observation text, `""` when nothing was executed |

The two empty strings are not a workaround: `StepMetrics` documents them as correct for "steps where
a field doesn't apply (e.g. no sandbox execution)".

Totals: `iterations = len(steps)`, `total_requests = Σ(1 + retries)` — real attempts, CORE-2's
retries included, which is what the field asks for — and both token totals by summation.
`total_time_seconds` comes from `clock()`.

### `api_url` verbatim

`StepMetrics.api_url` is described as the base URL; `LLMResponse.api_url` is deliberately the full
endpoint, which CORE-1 calls "per-step evidence of what was contacted". The two descriptions
disagree.

The moulinette never validates the field — `__main__.py` only prints it. Both readings therefore
pass, so the loop copies the full endpoint verbatim. When two options are equally compliant, the
more informative one wins; truncating would discard something CORE-1 produced on purpose.

### The limits that actually bind

The moulinette enforces four ceilings, and exceeding one invalidates the run whatever the answer:

| | MBPP | SWE-bench |
|---|---|---|
| `max_iterations` | 10 | 30 |
| `max_input_tokens` | 6 000 | 300 000 |

The comparison is `<=`, so ten full iterations pass. The 6 000-token input ceiling is crossed
*before* the tenth iteration under an append-only transcript, which is the pressure CORE-5 and
CORE-7 exist to relieve. CORE-4 cannot fix it and does not try; it records the totals that make the
crossing visible, and leaves the one seam — `compact` — through which it gets fixed.

## Testing

Scripted doubles, no I/O, exhaustive tables — the pattern CORE-1, CORE-2 and CORE-3 established.

- `FakeProvider` scripted on a list of `LLMResponse` / `ProviderError`, modelled on the one in
  `test_llm_retry.py`. `FakeSandbox` scripted on a list of `ExecResult`, recording the code it was
  given. `FakeClock` for `total_time_seconds`.
- **No test starts a process or opens a socket.** The loop is testable entirely in memory and must
  stay that way: the sandbox suite already spends real seconds proving real timeouts, and this
  suite has no reason to spend any.
- A parametrised table over the seven `Outcome` values plus the extraction failure: which
  observation, does the loop continue, what lands in `StepMetrics`.
- Accounting: `total_requests` counts `1 + retries`, the totals are sums, `iterations == len(steps)`.
- The never-raises obligation, tested in both directions. A sandbox that raises `RuntimeError`
  yields a valid `SolutionOutput` with `success=false`; a `KeyboardInterrupt` **propagates**. The
  second test is what proves the boundary catches `Exception` and not `BaseException` — without it
  that mutation survives.
- One wiring test using the real `extract_code`, which is pure, against a fake provider and sandbox.
- The existing import-boundary test covers `agent/` for free.

## Deferred

- **Budget guards.** CORE-5 adds token and wall-clock ceilings as value parameters, on the pattern
  CORE-2 used for `max_elapsed_seconds`.
- **History compaction.** CORE-7 supplies a real `compact`; the seam and its identity default ship
  here.
- **Structured feedback.** SBX-7 rewrites `observation.py`.
- **Reacting to a 413 by compacting and retrying.** The right response to an over-long context, and
  impossible before compaction exists. CORE-7 inherits the verbatim error message that says so.
- **Writing `solution.json` and container cleanup.** MBPP-1 and SWE-3, at the process entry point.
