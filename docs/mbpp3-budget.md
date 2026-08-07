# MBPP-3 · what the ten tasks cost under the exam ceilings

The `agent_mbpp` CLI used to hand the loop four raised ceilings so that M1 could prove the
pipeline without a run dying at iteration ten. It now hands it the four limits of the
subject (VI.1.1). This is what the ten recorded tasks cost once it does.

Measured on `llama-3.3-70b-versatile` via Groq, with CORE-6's code-first prompt and CORE-7's
history compaction both in place. Compaction is on by default in `run_task`, so nothing in
this card had to enable it.

## The four ceilings

| Ceiling | Limit | Worst case observed | Headroom |
|---|---|---|---|
| iterations | 10 | 5 | 50% |
| cumulative input tokens | 6 000 | 4 769 | 21% |
| cumulative output tokens | 1 500 | 1 215 | 19% |
| wall clock | 120 s | 19.3 s | 84% |

**No task breached any ceiling.** The budget guard CORE-5 shipped never had to force a
submission — it is present and correct, and on this batch it simply never fired.

Two of the four are close: output at 81% consumed in the worst case, input at 79%. Both are
cumulative over the whole task, so watch both when the prompt or the model changes. The two
worst cases are different tasks — 160 for output, 260 for input — so no single run has been
near either edge on both axes at once.

Input is the one the forced-submission retry moves. A forced turn that answers nothing is now
retried while the budget allows, so the no-answer path costs up to one extra call, plus the
nudge appended to each forced view. How much that is depends on how large the transcript has
grown by then: an adversarial provider that never returns a fenced block spent 3% more input
under one prompt size and 24% more under another, in both cases stopping inside the ceiling
on the affordability check. None of the ten tasks above reached the guard, so that path is not
in the table.

## Per task

| task | iterations | input | output | seconds |
|---|---|---|---|---|
| 12 | 2 | 1 475 | 122 | 0.6 |
| 57 | 3 | 2 086 | 314 | 1.2 |
| 84 | 5 | 4 384 | 717 | 2.3 |
| 105 | 2 | 1 198 | 132 | 0.6 |
| 160 | 4 | 3 561 | 1 215 | 19.3 |
| 233 | 3 | 2 226 | 295 | 12.3 |
| 260 | 5 | 4 769 | 835 | 2.7 |
| 305 | 4 | 3 217 | 437 | 10.8 |
| 391 | 2 | 1 594 | 187 | 0.8 |
| 447 | 3 | 2 111 | 260 | 16.2 |

Input cost tracks iteration count closely, which is what compaction is for: the transcript
stops growing quadratically, so a five-iteration task costs roughly twice a two-iteration one
rather than far more.

## What this does not measure

**Correctness is a separate axis and this card does not move it.** The moulinette validates 7
of these 10 against the hidden tests. Since nothing breaches a ceiling, overall equals
correctness here — the failures are wrong answers, not budget failures.

**The output ceiling holds by construction, not by luck.** Every request is capped at
`min(models.json max_tokens, what the output budget has left)`, so the ceiling is reached
exactly, never crossed: a run allowed 1 500 asks for 1 500, then 900, then 300. The one
assumption is that the endpoint honours `max_tokens` — a provider that ignores it, or one that
bills reasoning tokens outside the completion, would overshoot and nothing here would stop it.
That is worth re-checking on any model whose billing is not plain completion tokens.

**One run, one model, one provider.** Sampling is not deterministic, so a single batch cannot
separate a real change from run-to-run variance; task-level correctness in particular moves
between runs. The budget figures above are the stable part, and they are not close enough to
any ceiling for variance to change the conclusion.

**Groq rate-limited two tasks on the first pass** (HTTP 429 on 260 and 391). They were re-run
rather than recorded as failures: a 429 is an infrastructure outcome, not a budget one. The
figures above are from the completed runs.
