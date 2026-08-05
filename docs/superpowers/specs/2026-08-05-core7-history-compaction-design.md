# CORE-7 · History compaction — design

Hold the transcript flat across iterations, so a task that needs several turns does not spend its
whole input budget re-sending its own past.

## What this delivers

`compact_history()` — one pure function that rewrites the message list before each call. System
prompt and task prompt survive verbatim, the most recent steps survive verbatim, and older steps
shrink to the code they ran plus the observation it produced.

It fills the seam CORE-4 left: `run_task(compact=...)`, currently defaulted to `_unchanged`.

## Why the transcript, and not the observations

The card anticipated summarising a bloated middle. Measurement says otherwise. Replaying MBPP task
160 — seven iterations, the worst of a ten-task sample — the observations are 0 to 33 characters
each. The largest reads `The code ran and printed nothing.` All the weight is in the model's own
replies, up to 4 784 characters (~1 200 tokens) in a single step.

The budget that breaks is cumulative, not per-call. Per-call input on task 160 peaked at 3 743
tokens, comfortably under the 6 000 ceiling; the run died because those calls *sum*. Every iteration
re-sends the whole transcript, so a modest transcript sent seven times costs far more than the
ceiling allows.

That reframes the goal: not "shrink an occasional spike" but "keep the per-call size from growing at
all".

## Behaviour

The history reaching the seam is `[system, task, assistant₁, obs₁, …, assistantₙ, obsₙ]`.

| Segment | Treatment |
|---|---|
| `system`, `task` | verbatim, always |
| last `verbatim_steps` pairs | verbatim |
| older pairs | assistant replaced by a truncation marker plus its extracted code; observation kept |

```python
def compact_history(messages: list[Message], *, verbatim_steps: int = 1) -> list[Message]
```

A reduced step keeps the assistant role and reads:

```
[earlier step — code only, reasoning omitted]
def find_Max_Num(arr):
    ...
```

The marker names what was dropped rather than merely that something was, on the reasoning that a
model shown a gap it cannot account for may try to fill it. That is a hypothesis, not a measurement;
if reduced steps turn out to provoke restatement, the marker wording is the first thing to change.

`verbatim_steps` is a parameter rather than a constant because the two benchmarks do not share a
budget: MBPP allows 6 000 cumulative input tokens, SWE-bench 300 000. MBPP-3 and SWE-6 tune it
without touching this code.

Old assistant messages are re-extracted with CORE-3's `extract_code` rather than remembered. The
seam's signature carries messages and nothing else, and keeping the function pure of run state is
worth a regex over a few kilobytes.

## Effect, measured

Task 160's real transcript, replayed against the 6 000-token cumulative ceiling:

| Setting | Per-call input | Calls that fit |
|---|---|---|
| none (today) | 539, 797, 1993, 2346, 2443, 2780, 2804 | 4 |
| `verbatim_steps=2` | 539, 797, 1993, 2347, 1254 | 4 |
| `verbatim_steps=1` | 539, 797, 1993, 1157, 1255, 1500 | 6 |
| `verbatim_steps=0` | 539, 797, 804, 1158, 1164, 1501, 1525 | 6 |

Headroom grows from four iterations to six — a 50% gain, and the reason `verbatim_steps=1` is the
default.

**This does not make task 160 pass.** It needed seven iterations; six still fall short. Compaction
is necessary and not sufficient: the other half of the budget problem is the iteration count itself,
which CORE-6 owns through a prompt that drives the model to `run_tests` then `final_answer` in fewer
turns. The two cards compose; neither substitutes for the other.

The figures carry a small approximation: the task prompt is not stored in `SolutionOutput`, so its
length was derived from the first call's billed input. Conclusions hold well within that margin.

## Edge cases

| Case | Result |
|---|---|
| fewer steps than `verbatim_steps` | returned unchanged |
| `verbatim_steps=0` | every step reduced |
| no extractable code in an old reply | marker alone survives |
| odd-length history | handled without raising |

The seam is called at the top of an iteration, after the previous observation was appended, so an
odd length should not occur. It is handled anyway: this function must never be the reason a run
fails.

## Error handling

Never raises. `extract_code` already guarantees the same. Anything unexpected returns the history
unchanged — spending budget beats corrupting the transcript, because a corrupted transcript costs
the task while an oversized one only costs tokens.

## Wiring

`run_task`'s `compact` default changes from `_unchanged` to `compact_history`. The reasoning that
sets `DEFAULT_MAX_ITERATIONS` to MBPP's stricter ceiling applies here: a caller who forgets must not
be able to invalidate a run silently.

`tests/test_agent_loop.py` calls `run_task` 45 times, of which 5 pass `compact` explicitly — those
are unaffected. The exposure is the multi-step tests that assert on what reached the provider, three
sites in total. Each is checked against the new default and updated where compaction legitimately
changes the view; a test that breaks for any other reason is a finding, not a fixture to adjust.

## Testing

- Shape: system and task survive; the verbatim window is respected; markers appear on reduced steps.
- Flatness: across N synthetic steps, per-call size stops growing. This is the actual goal, so it is
  tested directly rather than inferred from the shape tests.
- Regression: task 160's recorded transcript replayed through compaction still fits six calls under
  6 000 tokens, locking in the measured gain.
- Edge cases from the table above.
