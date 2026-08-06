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
| cumulative input tokens | 6 000 | 4 384 | 27% |
| cumulative output tokens | 1 500 | 1 215 | 19% |
| wall clock | 120 s | 19.3 s | 84% |

**No task breached any ceiling.** The budget guard CORE-5 shipped never had to force a
submission — it is present and correct, and on this batch it simply never fired.

The tightest of the four is the output budget, at 81% consumed in the worst case. That is the
one to watch when the prompt or the model changes: it is also the ceiling that is cumulative
over the whole task, so a single verbose turn can spend it.

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

**One run, one model, one provider.** Sampling is not deterministic, so a single batch cannot
separate a real change from run-to-run variance; task-level correctness in particular moves
between runs. The budget figures above are the stable part, and they are not close enough to
any ceiling for variance to change the conclusion.

**Groq rate-limited two tasks on the first pass** (HTTP 429 on 260 and 391). They were re-run
rather than recorded as failures: a 429 is an infrastructure outcome, not a budget one. The
figures above are from the completed runs.
