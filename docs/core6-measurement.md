# CORE-6 · what the code-first prompt changed

Ten MBPP tasks, the same ten in both runs, solved end to end against
`llama-3.3-70b-versatile` on Groq. The only difference between run A and run B is the
prompt file: A carries the pre-CORE-6 text (`Thought:` then a code block), B carries the
code-first text this card ships. Same model, same day, same tasks.

## Result

| Measure | A — previous prompt | B — code-first |
|---|---|---|
| turns | 38 | **26** |
| turns producing no code | 1 | **0** |
| cumulative output tokens | 6 737 | **3 558** |
| cumulative input tokens | 48 853 | **19 951** |
| agent reported success | 9 of 10 | **10 of 10** |
| tasks breaching any exam ceiling | 1 | **0** |
| moulinette, correctness | 8 of 10 | 8 of 10 |
| moulinette, overall | 8 of 10 | 8 of 10 |

Per task, iterations A → B: 12 `2→2`, 57 `3→2`, 84 `3→3`, 105 `2→2`, 160 `5→3`,
233 `2→2`, 260 `14→5`, 305 `3→3`, 391 `2→2`, 447 `2→2`.

Nothing regressed. Task 260 is the clearest case: fourteen iterations ending in failure
under the old prompt, five ending in success under the new one. It is also the one task
that breached an exam ceiling under the previous prompt — 31 110 input tokens, 155 s — and
code-first leaves the batch with no breach at all, which is the row that matters most for
MBPP-3.

Correctness and overall agree here only by coincidence: run A's single breach fell on task
260, which was already failing its hidden tests, so no task was lost to limits alone.

## What did not change

Validation stays at 8 of 10. Tasks 160 and 260 fail their hidden tests under **both**
prompts, so those failures are not something code-first introduced — the model produces a
solution that satisfies the visible assertions and not the hidden ones. Fixing that is a
question of solution quality, not of turn shape.

## The confirmation run on nemotron

The A/B above runs on llama, and llama is not the only model this prompt will meet. The
same ten tasks were re-run with the code-first prompt against
`nvidia/nemotron-3-ultra-550b-a55b:free` on OpenRouter, against the four numbers the design
spec named as this card's validation criteria.

| Measure | baseline, previous prompt | code-first |
|---|---|---|
| turns producing no code | 4 of 28 | 8 of 31 |
| tasks over the 1 500-token output budget | 2 — 6 364 and 5 559 | 2 — 7 721 and 9 000 |
| iterations on the two slow tasks | 7 and 5 | 8 and 6 |
| moulinette, correctness | 10 of 10 | 9 of 10 |

**Code-first is worse than the previous prompt on all four, on this model.** The spec set the
last row as the guard rail — if validation drops, code-first broke something — and it drops.
What helps a model that answers directly hurts one that reasons at length.

Read against the exam's limit checks rather than correctness alone, the run scores **8 of 10
overall**. Eight tasks finish inside 2 289 input / 1 178 output / 31 s; the other two fail
every ceiling at once:

| task | iterations | input | output | time | correctness | overall |
|---|---|---|---|---|---|---|
| 160 | 8 | 37 853 | 7 721 | 254 s | PASSED | FAILED |
| 260 | 6 | 26 667 | 9 000 | 351 s | FAILED | FAILED |

All eight code-less turns stopped at exactly 1 500 output tokens — the per-call ceiling — and
all eight belong to those two tasks.

Two caveats on the comparison. The baseline column is quoted from the design spec, which
recorded it at the time; its raw `solution.json` files are no longer on disk, so unlike the
llama A/B it cannot be recomputed. And the baseline's `10 of 10` is correctness alone — the
same run already had two tasks over the output budget, so under the limit checks it was no
better than 8 of 10 overall either.

## The two providers, and what that does to the comparison

The llama A/B ran on Groq, the nemotron runs on OpenRouter. That split is not a design
choice: OpenRouter's free-tier daily quota was exhausted partway through this card's
measurement, so the A/B was moved to Groq to keep both of its arms on one provider on one
day, and the nemotron confirmation waited for the quota to reset.

What the split costs, and does not cost:

- The llama A/B is unaffected. Both arms ran on the same provider, the same model and the
  same day, so the prompt is the only variable in that table.
- The nemotron comparison holds for the same reason — both columns are the same model on
  the same provider — but with the caveat above about the baseline's artefacts.
- What cannot be read across the two tables is provider against provider. Groq and
  OpenRouter differ in ways this measurement never isolated: the free tier is quota-limited
  per key per day, and OpenRouter returns some errors inside an HTTP 200 body, which `llm/`
  already unwraps so they get retried. Model and provider are confounded, and nothing here
  separates them. Ranking providers is BENCH-1's job, which measures response time, retries
  and availability directly.

Both runs pass `--provider-url` explicitly, so each one resolved its own provider's key —
key discovery derives the variable prefix from the URL host — and neither run could have
silently borrowed the other's credentials.

## Why no per-call ceiling ships anyway

The obvious reading of the run above is that a per-call cap is needed after all. It is
not, and the reason is in the shape of the budget rather than in the model's behaviour.

The MBPP output ceiling of 1 500 tokens is **cumulative over the whole task**, so a single
turn can spend everything the task had. Task 160 spent 7 721 tokens across eight turns:
staying under budget would have meant averaging 187 tokens a turn, well below the largest
turn that legitimately produced code anywhere in this run — 1 049 tokens. A per-call cap
loose enough to spare real code turns cannot bring these tasks under a cumulative budget,
and one tight enough to try would truncate the turns that do the work.

What these two tasks need is the cumulative guard CORE-5 already shipped in
`agent/budget.py`, which the MBPP CLI still runs with the M1 ceilings raised. Switching it
back to the exam values belongs to MBPP-3, and this run is recorded on that card.

One measurement artefact is worth naming, because it inflates the apparent legitimate
maximum: task 260's sixth turn is counted as having produced code only because the model
never closed its fence, so extraction took the rest of the message. Read as a code turn it
suggests a legitimate 1 500; it is reasoning spillover, and 1 049 is the real maximum.

## The finding that belongs to another card

Nemotron is the more correct model on the hard cases and the markedly more expensive one.
Under the previous prompt it was correct on all ten, including 160 and 260, the two llama
misses; under code-first it is correct on nine against llama's eight. It pays for that with
6 364 output tokens on task 160 alone, against 1 067 for llama under the same prompt.

Which of the two the final pipeline runs is a model-selection question, and MBPP-4 owns it.
Recorded here because the measurement produced it, not because this card acts on it — but
with one observation MBPP-4 will want: a prompt is not model-agnostic. The same file that
cuts llama's output tokens by 47% costs nemotron a correct answer, so whichever model is
chosen, the prompt should be re-measured against it rather than assumed to carry over.

## Method note

An earlier attempt at this measurement was invalid and is not reported above. It replayed
the tasks with two changes at once — the new prompt and a per-call ceiling lowered to 400
tokens — and the ceiling truncated ten turns that had previously produced code, so the
comparison measured the ceiling rather than the prompt. The ceiling was justified by a
distribution that had never been measured: the claim was that no turn fell between 400 and
1 500 tokens, and in fact ten did. That change is reverted; only the prompt ships.

The run above changes one thing at a time, which is why it can answer the question.

## Limits

Ten tasks, one model, one run each. Two of the ten carry most of the difference. The
direction is consistent across every task and no task got worse, but this is a sample, not
a rate.
