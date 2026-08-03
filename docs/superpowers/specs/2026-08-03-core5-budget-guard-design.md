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
submission it forces is itself a round trip that has to fit. A check that trips only once the next
request is projected to cross the ceiling has already lost — it would force the submission by making
the very call that busts the budget, and an over-budget run is scored as a failure whatever it
answered. So:

| Ceiling | Buffer | Trips when |
|---|---|---|
| Input tokens | `DEFAULT_INPUT_MARGIN`, 15% | `total + estimated_next > max_input_tokens * 0.85` |
| Wall-clock | `DEFAULT_WALL_CLOCK_MARGIN`, 15% | `elapsed > max_wall_clock_seconds * 0.85` |
| Output tokens | `RESERVED_OUTPUT_TOKENS`, 300 | `remaining < 300` |

Output tokens get an absolute reserve rather than a percentage because what has to fit is a fixed
thing — one `final_answer(...)` with a code block — not a proportion of the budget.

The input margin also covers a property of `estimate_tokens` that runs the opposite way to
intuition: chars/4 is **optimistic**, not slack. It is the ratio for prose, code tokenises nearer 3
chars per token, and it ignores the per-message chat-template overhead the endpoint bills into
`prompt_tokens`. Sizing the buffer as if the estimate were conservative would put the guard on the
wrong side of the ceiling exactly when the transcript is longest.

**What the margin does not cover.** The guard acts on an estimate made *before* a call, so it bounds
the overshoot rather than eliminating it. Writing `E ≈ 0.75·U` for the estimate against real usage
`U`, and `C` for the ceiling: the guard trips at `total + E > 0.85·C`, and the forced call then costs
`U`, so the run finishes near `0.85·C + 0.25·U`. That stays under `C` as long as **one call's input
is under ~60% of the ceiling** — 3 600 tokens on MBPP, 180 000 on SWE-bench. Both hold by a wide
margin under any transcript this agent produces, and `CORE-7` only widens it. A single request large
enough to break that bound would blow the budget on its own, before any guard could see it coming.

## `budget.py`

```python
def estimate_tokens(messages: Sequence[Message]) -> int:
    """Approximate the token count of a message list as len(chars) / 4.

    A heuristic, not a tokenizer: no dependency, and no assumption about which
    model's vocabulary applies. The 4-per-token ratio is the standard rough
    estimate for English/code; the guard's margins are sized to absorb its
    error, not to make it exact.
    """


def remaining_output_tokens(spent: int, limit: int) -> int:
    """max(0, limit - spent)."""


def capped_max_tokens(default: int | None, remaining: int) -> int:
    """The smaller of the caller's own max_tokens and what the output budget
    has left, so a single verbose completion cannot overshoot the ceiling that
    every prior step has been building toward.
    """


RESERVED_OUTPUT_TOKENS = 300
"""Output budget held back so a forced attempt has room to answer. This is the
threshold the guard trips on, and it does the real work: the budget normally
drains a few hundred tokens per step, so stopping while this much is left
leaves room for a genuine fenced code block.
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
)
if reason is not None:
    if not can_attempt_submission(remaining_output):
        self.error = ...  # names the reason; no request is spent
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
- `MIN_VIABLE_OUTPUT_TOKENS` and `RESERVED_OUTPUT_TOKENS` are named constants, not magic numbers,
  each documented with the job it does: the reserve is what the guard trips on, the floor is what
  stops a request being spent when the reserve was overrun in a single completion.
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
- **A real tokenizer.** `estimate_tokens` stays a chars/4 heuristic; swapping in a model-specific
  tokenizer, if ever justified, is a change inside this one function.
- **Passing the remaining wall clock down to `CORE-2`.** `RetryingProvider` bounds each `complete()`
  with its own `max_elapsed_seconds`, restarted per call, and its docstring anticipates this card
  supplying a smaller value derived from what is left of the task. It does not: `LLMProvider.complete()`
  has no deadline parameter, so wiring one means changing the protocol every provider implements,
  which is more than this card should carry. The consequence is bounded but real — a retry storm plus
  a hung socket can consume more than the 15% wall-clock margin holds back, so the forced attempt
  itself can overrun the ceiling. Whoever adds a deadline to the protocol should take it; until then
  `retry.py` says so rather than promising a caller that does not exist.
