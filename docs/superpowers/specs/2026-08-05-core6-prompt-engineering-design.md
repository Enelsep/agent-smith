# CORE-6 · Prompt engineering: two prompts — design

Stop the model spending a whole task's output budget on reasoning it never turns into code.

## What this delivers

`src/agent_smith/prompts/mbpp.md` and `src/agent_smith/prompts/swebench.md`, both loaded at
runtime, and an MBPP prompt whose every turn is a code block.

The per-call token ceiling this document also designs is **not** part of what ships. See the
section on it below for what the measurement said once it was taken.

## The measurement that drives it

Ten MBPP tasks were solved end to end against `nvidia/nemotron-3-ultra-550b-a55b:free`, 28 turns
in total. Four of those turns produced no runnable code at all. All four had hit exactly 1 500
output tokens — the per-call ceiling. No turn under the ceiling failed to produce code, and no
turn at the ceiling produced any.

The model is not disobeying the prompt. Today's prompt already asks for "one or two sentences" of
Thought and already states that each turn is one Thought and one Code block. The model reasons
past the ceiling and is cut off before it reaches the fence.

Two facts make this worse than a wasted turn:

| | value |
|---|---|
| MBPP cumulative output budget | 1 500 tokens |
| `models.json` per-call `max_tokens` | 1 500 tokens |

One verbose turn is allowed to spend the entire run's output allowance. Measured against that
budget, task 160 finished at 6 364 output tokens (4.2×) and task 260 at 5 559 (3.7×). Task 233
solved correctly in two turns and still reached 1 395 — 93% of the ceiling.

The tail is where the cost lives. Eight of the ten tasks finish in two turns; the two that do not
took seven and five, and they carry every code-less turn between them.

## Code first

The turn *is* the code block. No prose precedes it and none follows; reasoning moves inside, as
comments. There is no longer a syntactic place to ramble before writing code, because code is the
first thing expected.

The `Thought:` marker disappears from the prompt. Nothing in CORE-3 depends on it: the only
occurrence in `extraction/strategies.py` is a boundary in the ReAct strategy's `Action Input`
terminator, which governs a different reply format and never reads our fenced blocks. Verified
against the current extractor.

```python
# Try the two given examples first.
def add(a, b):
    return a + b


print(add(2, 3), add(-1, 1))
```

Truncation stops being fatal under this shape. CORE-3 recovers a fenced block whose closing fence
never arrived, provided the fragment parses — verified against the current extractor. A cut that
lands mid-expression still fails, but it fails with the message CORE-3 already writes for the
model rather than with silence.

**This is the risky part of the change.** Eight of ten tasks already succeed in two turns under
the current format; we are altering what works to repair the tail. The validation section below
exists to catch that.

## Where the prompts live

`src/agent_smith/prompts/*.md`, read through `importlib.resources`.

The card names `prompts/` at the repository root. Nothing external requires that path — neither
the subject nor the evaluation scripts read it, only the CLI does. A file inside the package ships
in the wheel automatically; one at the root needs a `force-include` entry, which is the same
mechanism that let a stale installed copy of `mcp_tools_swebench.py` shadow the repository's own
file and produce a failure that looked like a defect in someone's branch. The packaging trap is
avoidable and worth avoiding.

## The per-call ceiling — designed, measured, dropped

**This section is kept for its reasoning, not as a description of the code. Do not implement
it.** Two things it asserts turned out to be false, and both were only discoverable by
measuring:

- **The distribution below is wrong.** Ten turns did land between 400 and 1 500 tokens. A cap
  at 400 truncated every one of them, all of which had been producing code.
- **A per-call cap cannot do this job at all.** The MBPP output budget of 1 500 tokens is
  cumulative over the whole task, so no cap loose enough to spare real code turns can hold a
  runaway task under it. The guard that can is the cumulative one CORE-5 already shipped;
  putting the exam ceilings back in the MBPP CLI is MBPP-3.

`docs/core6-measurement.md` carries both findings. What follows is the argument as it stood
before the measurement.

`models.json` drops `max_tokens` from 1 500 to 400 for every model in the catalogue.

Against a 1 500-token cumulative budget, 400 means no single turn can claim more than a quarter of
it, leaving room for three or four turns. The corpus supports the number: 24 of 28 observed turns
already finished under 400 tokens, and nothing at all landed between 400 and 1 500. The ceiling
therefore only bites the four pathological turns.

The change applies to every entry in the catalogue, not only the model that was measured. The
argument is arithmetic rather than behavioural — no model should be able to spend a whole run's
output allowance on one turn — so it holds without a per-model measurement. If a future model
turns out to need more room, the catalogue already carries `max_tokens` per model and can say so
there.

This composes with code-first rather than substituting for it. On its own a lower ceiling would
only make each wasted turn cheaper; it is code-first that makes the truncated turn recoverable.

## What the MBPP prompt must say

Beyond the format, two rules the audit asked for:

- **No fitting to the visible assertions.** Partially present today and to be stated harder: the
  visible tests are a subset, hidden tests decide the outcome.
- **The submitted source carries its own imports.** One task in the corpus needed a module
  (`math`) and did include the import, so this is insurance rather than a demonstrated failure —
  but the prompt currently says nothing about it, and the cost of saying it is a line.

The allowlist stays interpolated from the sandbox configuration rather than hardcoded, as it is
today, so a model is never told it may import something the sandbox will refuse.

## What the SWE-bench prompt must say

The same skeleton — code first, the same fence and stop contract — plus the nine MCP tools and
`get_patch` as the terminal step. Nothing beyond that.

No resolution method is written, because none can be tested: there is no `agent_swebench` CLI yet
(SWE-3 is blocked on SWE-1 and MCP-4). A six-step method invented here would be six untested
hypotheses shipped as instructions. The file states in its own text that it is unvalidated, and
SWE-5 fills it in against real failure traces.

## Validation

The ten recorded tasks are replayed and four numbers compared against the baseline:

| Measure | Baseline |
|---|---|
| turns producing no code | 4 of 28 |
| tasks over the 1 500-token output budget | 2 (6 364 and 5 559) |
| iterations on the two slow tasks | 7 and 5 |
| moulinette validation | 10 of 10 |

The last row is the guard rail. If validation drops, code-first broke what was working and no
improvement in the tail buys that back.

The sample is ten tasks and the conclusions rest on two of them. The correlation between
code-less turns and the token ceiling is clean — four out of four, with no counterexample — but
it is a direction, not a law.

## Stop sequences

`models.json` already carries stop sequences per model, and all three entries share the same list.
No mechanism is missing. `<end_code>` stays the fence closer and the stop token, unchanged, and is
checked for consistency with the new turn shape rather than rewritten.

## Out of scope

- **Choosing a different model.** The verbosity measured here is this model's behaviour; another
  may not need any of it. That comparison is MBPP-4.
- **Lowering the budget targets.** The audit recommends 4 000 / 1 000 / 60 s against the subject's
  6 000 / 1 500 / 120 s. This card is designed against 6 000 / 1 500 / 10, confirmed as the target.
