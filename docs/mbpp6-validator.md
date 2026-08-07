# MBPP-6 · what the submission validator refuses, and what that is worth

The harness gives itself the last word on a submission: a run ends on `final_answer` only
if the answer survives the task's own assertions. This is what that refusal is made of, and
what changing it measured.

Two passes. First a replay of the validator over solutions two full MBPP batches had already
produced, which costs nothing and touches no provider. Then a live re-run of the tasks the
replay flagged, to see whether the model does anything different with what it is now told.

## The batches the replay reads

All 257 MBPP tasks, run per task under the four ceilings of the subject (VI.1.1), before any
of this card's changes:

| Model | Passing | Empty submissions |
|---|---|---|
| `mistral-medium-latest` | 233 / 257 | 10 |
| `codestral-2508` | 205 / 257 | 0 |

## What the refusal says now

A failed assertion used to reach the model as a bare `AssertionError`. The sandbox `exec`s a
string, so the traceback can only point at `<string>`, and the exception carries no message
of its own — the model was told it had failed without being told which case:

```
Traceback (most recent call last):
  File "<string>", line 12, in <module>
AssertionError
```

The assertions now run one at a time and the one that raised is quoted into the refusal.
Replayed over the 514 recorded solutions:

| | Mistral | Codestral |
|---|---|---|
| refusals | 7 | 45 |
| of those, naming the assertion that failed | 7 | 45 |

## What the refusal is judged against

The submitted string is executed in a worker restarted for the purpose. Without that, it is
judged in the namespace the loop has been driving, where every helper the model defined
along the way is still bound: a submission leaning on one of them passes the check here and
raises `NameError` in front of the grader. That is the single failure this whole mechanism
exists to catch, so it cannot be the one it is blind to.

For the same reason a submission that raises out of its own first block — one carrying a
`final_answer(...)` call, say — is refused rather than waved through. No assertion ran, so
nothing was verified, and `final_answer` does not exist where the solution will be run.

## What the re-run measured

The seventeen Mistral tasks the replay flagged — ten that submitted nothing, seven refused —
re-run on `mistral-medium-latest`, scored against every assertion the grader runs, the
hidden one included:

| | before | after |
|---|---|---|
| empty submissions | 10 | 7 |
| correct | 0 | 6 |

Four of the seven refusals now pass. Naming the assertion is what the model acts on: the
refusal it used to read named a failure it could not locate.

The other two gains come from elsewhere in the card. A block holding only comments parses,
runs and prints nothing, so the sandbox has no complaint to make and the model used to read
"The code ran and printed nothing" — it now reads that comments do not run. And a reply the
endpoint stopped at the token cap (`finish_reason == "length"`) is named as truncated
rather than reported as a missing code block.

## What it does not fix

The seven that still submit nothing spend the whole 1 500-token output budget on a single
completion — 45 to 85 lines of commentary, cut mid-sentence. The run ends before a second
turn exists, so no observation, however well worded, is ever read.

Lowering the per-call ceiling is the obvious lever and it does not pay. With it at 500,
six of the seven submit something and none of the six is correct. These are tasks the model
does not know how to solve; the budget is where that shows, not why.

## What this evidence is not

A refused submission is not the same as a wrong one, and the reverse also holds: the
assertions the agent sees are a subset, so passing them says less than failing them does.
Everything above is scored against the full assertion list, but in our own sandbox rather
than in the grader's container — same assertions, different environment. It ranks a change;
it does not replace `moulinette validate`.
