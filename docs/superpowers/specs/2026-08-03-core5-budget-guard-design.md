# CORE-5 · Budget guard — design

Runs before each LLM call: estimate what the call will cost, and if it would blow the task's input
token, output token, or wall-clock ceiling, stop and force one last submission attempt instead of
silently going over — an over-budget run is scored as a failure regardless of correctness, so
submitting whatever the agent has is strictly better than not submitting at all.

## What this delivers

Three new value parameters on `run_task()` — `max_input_tokens`, `max_output_tokens`,
`max_wall_clock_seconds` — and a new module, `agent_smith/agent/budget.py`, holding the pure
functions that decide when the ceiling is close enough to act on. `loop.py` calls them from inside
`_Run.execute()`; nothing about the shape of `run_task()`'s return value or its never-raises
contract changes.

## Scope boundary

This is the seam CORE-4 named for it: "CORE-5 adds token and wall-clock ceilings as value
parameters, on the pattern CORE-2 used for `max_elapsed_seconds`." Two things follow from that:

- The three ceilings are plain keyword arguments with MBPP-shaped defaults, not a bundled config
  object. `MBPP-1` and `SWE-3` pass the numbers for their own benchmark, exactly as they already do
  for `max_iterations`.
- `CORE-5` does not touch `KeyPool` or `RetryingProvider`. `CORE-2` already owns *provider-side*
  rate limits (a key's per-minute/per-day cap, handled by rotation and cooldown); this card owns the
  *task-level* evaluation ceiling the subject and the moulinette impose, which is a completely
  separate number that exists regardless of which key served the request.

`CORE-5` blocks `CORE-7` (history compaction) and, transitively, `MBPP-3`. It does not depend on
`CORE-7`: the guard estimates whatever `compact(history)` currently returns, including today's
identity default, so it needs no change when `CORE-7` lands — only a better estimate to work with.

### What values to target

The guard targets the raw ceilings the subject and the moulinette's code enforce — 6 000 input /
1 500 output / 120 s for MBPP, 300 000 / 10 000 / 900 s for SWE-bench — not the stricter figures
that appear in the moulinette's README (4 000 / 1 000 / 60 s). That mismatch is a documentation
inconsistency the team decided not to design around. Defaults on `run_task()` are the MBPP numbers,
the stricter of the two benchmarks, on the same reasoning as `DEFAULT_MAX_ITERATIONS = 10`: a caller
that forgets to pass SWE-bench's values cannot silently invalidate a run by undershooting.

The 15% margin the card asks for is a separate thing from that decision and stacks with it: it is a
fixed operational buffer *inside* whichever ceiling is targeted, not a comment on which ceiling
number is correct.

**Every ceiling gets a buffer, not just wall-clock.** The card names the margin under wall-clock, but
the reason for it is not specific to time: the guard's whole job is to submit *under* budget, and the
submission it forces is itself a round trip that has to fit. An over-budget run is scored as a
failure whatever it answered, so the guard has to stop while there is room for the call it is about
to ask for. So:

| Ceiling | Buffer | Trips when |
|---|---|---|
| Input tokens | the forced round trip, priced in billed tokens, plus `DEFAULT_INPUT_MARGIN` (15%) | `total + ratio·(2·E + growth + nudge) > max_input_tokens * 0.85` |
| Wall-clock | `DEFAULT_WALL_CLOCK_MARGIN`, 15% | `elapsed > max_wall_clock_seconds * 0.85` |
| Output tokens | `output_reserve(max_output_tokens)` | `remaining < reserve` |

### The input guard, and the two conversions it cannot skip

`total_input_tokens` and the ceiling are denominated in **billed** tokens. `estimate_tokens` returns
something else — a character count over a divisor. Comparing the two directly is the mistake that
survived two rounds of this design, in two different forms, so it is worth stating what the guard
actually has to do.

The question is: *if I authorise this call, can I still afford the forced submission that follows
it?* Answering it needs both costs in billed tokens:

- **The authorised call** costs `ratio · E`, where `E` is the estimate of the view about to be sent.
- **The forced call** is not that view. By the time it is made, the model's reply and the resulting
  observation have joined the transcript, so it costs `ratio · (E + growth + nudge)`.

Leave out `ratio` and the reservation under-counts by however much the endpoint bills above the
divisor. Leave out `growth` and it reserves the wrong view — the one before a full round trip was
appended to it. Both were left out at some point, and both produced runs that finished over the
ceiling while reporting that the guard had stopped them.

Neither quantity needs to be guessed. `ratio` is `billed ÷ estimated`, and both numbers are known
after every call — see `billing_ratio`, floored at 1.0 and pessimistic until the first call has been
billed. `growth` is the largest increase in `E` observed between iterations. The loop measures both
and passes them in; `budget.py` stays pure.

Sweeping 2 205 configurations — system prompts of 100–8 000 characters, replies of 100–2 400,
observations of 100–1 500, and endpoints billing between 4.0 and 1.6 chars per token — every run
finishes under a 6 000-token ceiling, and the iterations reached degrade smoothly with density
(3.9 at 4.0 chars/token, 3.3 at 3.0, 2.3 at 1.6) rather than the budget being blown.

**A last check before the forced call.** Reserving ahead makes it rare, but a transcript can still
reach a point where even the forced request will not fit. It is then not made:
`can_afford_forced_call` compares it against the ceiling itself, and a run that would cross the
ceiling is scored a failure whatever it answers, so the request cannot help. This is the input
counterpart of `can_attempt_submission`.

**Exactly how far the guarantee reaches.** It is worth being precise, because this is the property
three review rounds were spent on:

> The run finishes under the input ceiling **unconditionally once one call has been billed**. Before
> that, it holds while the endpoint bills at most `UNCALIBRATED_BILLING_RATIO` (1.6) × the estimate.

The exception is the first call and only the first call, because that is the one priced with a guess
rather than a measurement. It bites only when the opening prompt is itself a large fraction of the
whole cumulative ceiling *and* the endpoint bills denser than 2.5 chars per token — measured, a
14 800-character system prompt against a 6 000-token ceiling finishes at 5 998 when the guess is
right and overshoots when it is not. No MBPP or SWE-bench prompt comes close to that shape, and a
first request that alone consumes the task's whole budget is not a case any guard can rescue.

**Why the estimator's divisor barely matters now.** chars/4 is the prose ratio and under-counts code.
That was worth arguing about while the guard compared estimates against a billed ceiling; once the
conversion is measured, a wrong divisor is absorbed from the second call onward. What the estimate
must never be read as is a billed figure. Content that tokenises far denser than any divisor
predicts — dense punctuation, base64, non-Latin script — is handled by that measurement rather than
by picking a different constant.

**The ratio is the worst seen, not the average.** `billing_ratio` is computed per call and the run
keeps the maximum. A cumulative average would lag a ratio that rises during a run — longer prompts
tokenising denser than short ones — and lagging here means under-reserving. Keeping the worst
observation costs a little headroom and removes the failure mode.

**Why the output reserve is derived, not constant.** What has to fit is one `final_answer(...)`, and
how big that is depends on the benchmark: MBPP submits a function (80–250 tokens), SWE-bench submits
a git patch (500–2 000). `output_reserve` is `max(300, 15% of the ceiling)`, landing on 300 for
MBPP's 1 500 and 1 500 for SWE-bench's 10 000. A flat constant sized for MBPP would guarantee a
truncated patch on exactly the branch that exists to salvage a SWE-bench run.

The reserve costs nothing on a healthy run: it binds only once the output budget is nearly spent,
which on a run converging in 2–3 iterations never happens. When it does bind, the run was failing
anyway, and trading a further iteration for an attempt that can actually emit a complete answer is
the better trade.

## `budget.py`

```python
def estimate_tokens(messages: Sequence[Message]) -> int:
    """Approximate the token count of a message list as len(chars) / 4.

    A heuristic, not a tokenizer: no dependency, and no assumption about which
    model's vocabulary applies. It is biased *low*, so it must never be read as
    a conservative upper bound; DEFAULT_INPUT_MARGIN is sized to cover that.
    """


def remaining_output_tokens(spent: int, limit: int) -> int:
    """max(0, limit - spent)."""


def capped_max_tokens(default: int | None, remaining: int) -> int:
    """The smaller of the caller's own max_tokens and what the output budget
    has left, so a single verbose completion cannot overshoot the ceiling that
    every prior step has been building toward.
    """


def output_reserve(max_output_tokens: int) -> int:
    """Output budget held back so a forced attempt has room to answer, as
    max(MIN_OUTPUT_RESERVE, OUTPUT_RESERVE_FRACTION * ceiling). This is the
    threshold the guard trips on, and it does the real work.
    """


MIN_VIABLE_OUTPUT_TOKENS = 20
"""Floor below which a forced attempt is not worth a request. The reserve
normally keeps the budget clear of this; two paths reach it anyway — a single
step draining the budget from above the reserve in one completion, and a
caller configuring a ceiling smaller than the reserve.
"""


def can_attempt_submission(remaining_output_tokens: int) -> bool:
    """Whether a forced attempt has the output budget to be worth making. A
    call capped below MIN_VIABLE_OUTPUT_TOKENS cannot return a
    `final_answer(...)`, and one capped at zero is rejected outright by most
    OpenAI-compatible endpoints; either way it spends input tokens and wall
    clock the run no longer has.
    """


def should_force_submission(
    *,
    total_input_tokens: int,
    estimated_next_input: int,
    max_input_tokens: int,
    elapsed_seconds: float,
    max_wall_clock_seconds: float,
    remaining_output_tokens: int,
    reserved_output_tokens: int,
    input_margin: float = DEFAULT_INPUT_MARGIN,
    wall_clock_margin: float = DEFAULT_WALL_CLOCK_MARGIN,
) -> str | None:
    """None if the next call fits comfortably; otherwise a short label naming
    which ceiling triggered the stop: "input_tokens", "wall_clock", or
    "output_tokens". The label, not a bare bool, is what lets the loop write
    an error message that says which budget ran out — the failure-category
    breakdown CORE-4's plan card asks BENCH/MBPP-5 to report on.
    """


FORCED_SUBMISSION_NUDGE = (
    "..."  # placeholder wording; CORE-6 owns the real text for both prompts
)
```

`estimate_tokens` and `capped_max_tokens` are pure functions of their arguments; nothing in this
module opens a socket, spawns a process, or can raise for a reason other than a caller passing the
wrong type. That is what keeps it out of `_Run.execute()`'s `try/except` boundary — there is nothing
here for that boundary to catch.

## Integration into `loop.py`

`_Run` gains two running totals, updated once per recorded step rather than recomputed by summing
`self.steps` on every call:

```python
# _Run.__init__
self.total_input_tokens = 0
self.total_output_tokens = 0

# _Run._record(), alongside self.steps.append(...)
self.total_input_tokens += answer.input_tokens
self.total_output_tokens += answer.output_tokens
```

`to_solution()` reads these two fields directly instead of `sum(step.input_tokens for step in
self.steps)` / the output equivalent. This is a small refactor of existing CORE-4 code, motivated
directly by this card: the guard needs the running total *during* the loop, not only at the end, and
one incrementally maintained counter is a single source of truth instead of two call sites that must
independently stay in sync with each other.

At the top of each iteration in `_Run.execute()`, before calling the provider:

```python
remaining_output = remaining_output_tokens(self.total_output_tokens, max_output_tokens)
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
        self.error = ...  # names output_tokens; no request is spent
        return
    view = [*view, {"role": "user", "content": FORCED_SUBMISSION_NUDGE}]

answer = provider.complete(
    view, max_tokens=capped_max_tokens(max_tokens_per_call, remaining_output)
)
```

Everything after this — appending the assistant message, extraction, sandbox execution, recording
the `StepMetrics` — is unchanged from CORE-4. The one new thing: if `reason` was not `None` this
iteration, the loop does not continue to the next one after recording this step, regardless of
outcome:

- If this step's execution reaches `FINAL_ANSWER` with a value, it is treated exactly like any other
  successful step — `success=True`. This is the entire point of the forced attempt.
- If it does not, `self.error` is set to a message naming `reason` explicitly, e.g. `"stopped by the
  {reason} budget guard at step N; no final answer was received"`, and the run ends the same way
  `max_iterations` exhaustion already does.

`capped_max_tokens` runs on *every* call, not only a forced one — output budget is enforced
proactively throughout the run, shrinking the ceiling passed to the provider as prior steps consume
it, rather than only being checked after the fact on the last step.

Only one forced attempt ever happens: the check runs once per iteration, and the iteration that
trips it is also the last one the loop runs.

### The nudge goes in the view, not the transcript

`compact` is called **once per iteration**, and the nudge is appended to what it returned rather than
to `self.history`. Two consequences, both of which matter to `CORE-7` rather than to this card:

- `compact` need not be idempotent or cheap. A compaction that caches, counts, or spends an LLM call
  to summarise runs exactly once per turn.
- `compact` need not preserve the last message it is handed. A compaction that keeps a head window,
  or folds the tail into a single summary, would otherwise swallow the nudge and silently un-force
  the forced attempt.

Nothing reads the message back, because the forced iteration is the last one — so keeping it out of
the transcript costs nothing and removes two obligations `CORE-5` would otherwise impose on the card
it is supposed to unblock.

### `max_tokens_per_call`

`run_task()` takes the operator's configured per-request ceiling — `ResolvedConfig.max_tokens`, from
`models.json` — and passes it as `capped_max_tokens`'s `default`. The loop lowers it as the
cumulative budget drains but never raises it, so a model configured to answer in 400 tokens is not
handed 1 500 on the first step. `None` leaves the provider's own default in force.

Passing a value on every call is what makes this parameter necessary: `OpenAICompatProvider` applies
its configured default only when the per-call argument is `None`, so a loop that always passes an
`int` would otherwise make the configured value unreachable.

## Error handling

- `budget.py` cannot raise for a data-dependent reason — the never-raises boundary in
  `_Run.execute()`/`run_task()` needs no change.
- A forced attempt that succeeds is indistinguishable from a normal success.
- A forced attempt that fails produces an `error` string naming which of the three budgets triggered
  it, distinct from the existing `max_iterations`-exhausted message, so a local failure log (feeding
  `MBPP-5`'s category breakdown later) can tell the two apart.
- The reserve and `MIN_VIABLE_OUTPUT_TOKENS` are named, not magic, each documented with the job it
  does: the reserve is what the guard trips on, the floor is what stops a request being spent when
  the reserve was overrun in a single completion.
- When the floor is hit, no request is made at all, so the `error` still names the budget rather than
  being overwritten by whatever the endpoint said about an unanswerable request.

## Testing

Same pattern CORE-1 through CORE-4 established: scripted doubles, no I/O, exhaustive tables.

- New `tests/test_agent_budget.py`, pure unit tests against `budget.py` directly:
  - `estimate_tokens` against strings of known length.
  - `capped_max_tokens`: `default=None`, `default` smaller than remaining, `default` larger than
    remaining.
  - `remaining_output_tokens`: floors at 0, never negative.
  - `can_attempt_submission` either side of the floor, and at zero.
  - `should_force_submission`: one case per trigger and the `None` case, each asserting the returned
    label. Every threshold is tested on **both** sides — tripping past the margin and *not* tripping
    at it — because a guard that fires one call late is indistinguishable from a correct one on a
    test that only checks the tripping side.
  - Both precedence pairs, so the documented check order is pinned rather than incidental.
- Extensions to `tests/test_agent_loop.py`:
  - `FakeClock.advance()` (already present) crosses the wall-clock margin at a chosen iteration;
    assert the nudge appears in the messages `FakeProvider` received, the run stops after that step,
    and `error` names `"wall_clock"`.
  - A scripted `FakeProvider` response sequence whose token counts cross the input margin; same
    assertions, `error` names `"input_tokens"`. Paired with an assertion that the finished run's
    `total_input_tokens` is **still under the ceiling** — the property the guard exists for, which no
    assertion about the error message alone would catch.
  - A multi-step scenario draining the output budget: assert the `max_tokens` `FakeProvider` receives
    shrinks step over step. Requires extending `FakeProvider` to record `max_tokens` per call
    alongside the messages it already records — a change to the test double, not to
    `LLMProvider`'s contract.
  - The output branch end to end, in all three of its shapes: tripping the reserve with room left to
    answer (the call is made, capped at what the reserve held back), draining the budget below the
    floor in one completion (no request is made at all), and a configured ceiling below the floor
    (no request on the first iteration either).
  - A forced attempt whose sandbox result is `FINAL_ANSWER`: `success=True`, same as an unforced run.
  - A forced attempt whose reply contains no code, covering the other branch of the iteration body.
  - `max_tokens_per_call` honoured: the per-call ceiling is never raised above the configured value.
  - Two `compact` contracts, both of which exist to protect `CORE-7`: it is invoked exactly once per
    iteration including the forced one, and the nudge still reaches the provider under a compaction
    that drops everything but a head window.
  - Existing assertions about `total_input_tokens`/`total_output_tokens` continue to hold with the
    incremental counters in place of the `sum()` — this is the regression check for the `_Run`
    refactor in "Integration into `loop.py`".

## Deferred

- **The exact wording of `FORCED_SUBMISSION_NUDGE`.** `CORE-6` owns prompt content for both
  benchmarks; this card ships a placeholder string and the seam, not the final text.
- **A real tokenizer.** Rejected rather than postponed. `tiktoken` encodes for OpenAI models, while
  we run Llama, Qwen and Mistral on Groq and OpenRouter — precision against the wrong vocabulary. It
  also fetches its BPE files over the network on first use, which would contradict the README's
  statement that our only outbound HTTP is the inference call and trip
  `tests/test_llm_import_boundary.py`. A per-model tokenizer from `transformers` would need each
  model's vocabulary files, for an accuracy the reservation already makes cheap to do without.
- ~~**Calibrating the estimate from observed usage.**~~ Was listed here as a refinement worth having
  once compaction had settled. It is not a refinement: without it the guard compares an estimate
  against a billed ceiling, and runs finish over budget on ordinary transcripts. It ships in this
  card — see "The input guard, and the two conversions it cannot skip".
- **Passing the remaining wall clock down to `CORE-2`.** `RetryingProvider` bounds each `complete()`
  with its own `max_elapsed_seconds`, restarted per call, and its docstring anticipates this card
  supplying a smaller value derived from what is left of the task. It does not: `LLMProvider.complete()`
  has no deadline parameter, so wiring one means changing the protocol every provider implements,
  which is more than this card should carry. The consequence is bounded but real — a retry storm plus
  a hung socket can consume more than the 15% wall-clock margin holds back, so the forced attempt
  itself can overrun the ceiling. Whoever adds a deadline to the protocol should take it; until then
  `retry.py` says so rather than promising a caller that does not exist.
