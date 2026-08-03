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

The 15% wall-clock margin the card asks for is a separate thing from that decision and stacks with
it: it is a fixed operational buffer *inside* whichever ceiling is targeted, absorbing the
imprecision of the token estimate and the duration of the forced attempt itself, not a comment on
which ceiling number is correct. It applies to wall-clock only — the card names it there
specifically. The input-token check compares directly against its ceiling with no added margin; the
chars/4 estimate's own slack is what stands in for one.

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

MIN_VIABLE_OUTPUT_TOKENS = 20
"""Roughly the shortest a `final_answer(...)` call can be written in. Below
this, capping max_tokens to the exact remainder no longer buys a real chance
at a completion — the guard trips on the same reasoning as running out of
input budget or wall clock, not because a lower cap is unsafe.
"""

def should_force_submission(
    *,
    total_input_tokens: int,
    estimated_next_input: int,
    max_input_tokens: int,
    elapsed_seconds: float,
    max_wall_clock_seconds: float,
    remaining_output_tokens: int,
    wall_clock_margin: float = 0.15,
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
view = compact(self.history)
estimated_input = estimate_tokens(view)
elapsed = self._clock() - self._started
remaining_output = remaining_output_tokens(self.total_output_tokens, max_output_tokens)

reason = should_force_submission(
    total_input_tokens=self.total_input_tokens,
    estimated_next_input=estimated_input,
    max_input_tokens=max_input_tokens,
    elapsed_seconds=elapsed,
    max_wall_clock_seconds=max_wall_clock_seconds,
    remaining_output_tokens=remaining_output,
)
if reason is not None:
    self.history.append({"role": "user", "content": FORCED_SUBMISSION_NUDGE})

max_tokens_for_call = capped_max_tokens(configured_default_max_tokens, remaining_output)
answer = provider.complete(compact(self.history), max_tokens=max_tokens_for_call)
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

## Error handling

- `budget.py` cannot raise for a data-dependent reason — the never-raises boundary in
  `_Run.execute()`/`run_task()` needs no change.
- A forced attempt that succeeds is indistinguishable from a normal success.
- A forced attempt that fails produces an `error` string naming which of the three budgets triggered
  it, distinct from the existing `max_iterations`-exhausted message, so a local failure log (feeding
  `MBPP-5`'s category breakdown later) can tell the two apart.
- `MIN_VIABLE_OUTPUT_TOKENS` is a named constant, not a magic number, documented as the point below
  which capping `max_tokens` to the literal remainder stops being a real chance at a completion.

## Testing

Same pattern CORE-1 through CORE-4 established: scripted doubles, no I/O, exhaustive tables.

- New `tests/test_agent_budget.py`, pure unit tests against `budget.py` directly:
  - `estimate_tokens` against strings of known length.
  - `capped_max_tokens`: `default=None`, `default` smaller than remaining, `default` larger than
    remaining.
  - `remaining_output_tokens`: floors at 0, never negative.
  - `should_force_submission`: one case per trigger (input tokens, wall clock, output floor, none),
    asserting both the returned reason and the `None` case.
- Extensions to `tests/test_agent_loop.py`:
  - `FakeClock.advance()` (already present) crosses the wall-clock margin at a chosen iteration;
    assert the nudge appears in the messages `FakeProvider` received, the run stops after that step,
    and `error` names `"wall_clock"`.
  - A scripted `FakeProvider` response sequence whose token counts cross `max_input_tokens` before
    `max_iterations` would; same assertions, `error` names `"input_tokens"`.
  - A multi-step scenario draining the output budget: assert the `max_tokens` `FakeProvider` receives
    shrinks step over step. Requires extending `FakeProvider` to record `max_tokens` per call
    alongside the messages it already records — a change to the test double, not to
    `LLMProvider`'s contract.
  - A forced attempt whose sandbox result is `FINAL_ANSWER`: `success=True`, same as an unforced run.
  - Existing assertions about `total_input_tokens`/`total_output_tokens` continue to hold with the
    incremental counters in place of the `sum()` — this is the regression check for the `_Run`
    refactor in "Integration into `loop.py`".

## Deferred

- **The exact wording of `FORCED_SUBMISSION_NUDGE`.** `CORE-6` owns prompt content for both
  benchmarks; this card ships a placeholder string and the seam, not the final text.
- **A real tokenizer.** `estimate_tokens` stays a chars/4 heuristic; swapping in a model-specific
  tokenizer, if ever justified, is a change inside this one function.
