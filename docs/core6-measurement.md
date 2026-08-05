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
| tasks exceeding 1 500 output tokens | 1 | **0** |
| agent reported success | 9 of 10 | **10 of 10** |
| moulinette validation, hidden tests | 8 of 10 | 8 of 10 |

Per task, iterations A → B: 12 `2→2`, 57 `3→2`, 84 `3→3`, 105 `2→2`, 160 `5→3`,
233 `2→2`, 260 `14→5`, 305 `3→3`, 391 `2→2`, 447 `2→2`.

Nothing regressed. Task 260 is the clearest case: fourteen iterations ending in failure
under the old prompt, five ending in success under the new one.

## What did not change

Validation stays at 8 of 10. Tasks 160 and 260 fail their hidden tests under **both**
prompts, so those failures are not something code-first introduced — the model produces a
solution that satisfies the visible assertions and not the hidden ones. Fixing that is a
question of solution quality, not of turn shape.

## The finding that belongs to another card

The morning baseline ran these same ten tasks against
`nvidia/nemotron-3-ultra-550b-a55b:free` with the previous prompt, and validated **10 of
10** — including 160 and 260, the two llama fails here. Nemotron is markedly more verbose
(6 364 output tokens on task 160 alone, against 1 067 for llama under the same prompt) and
markedly more correct on the hard cases.

That is a model-selection result, and MBPP-4 owns it. It is recorded here because the
measurement produced it, not because this card acts on it.

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
