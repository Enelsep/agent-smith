# CORE-3 · Multi-format code extraction — design

**Ticket:** AGE-11 · **Depends on:** SETUP-2 (AGE-58) · **Feeds:** CORE-4, CORE-6, BENCH-3

## What this delivers

One pure function from text to text. In: whatever the model wrote. Out: Python the sandbox can
execute, or a failure that says why in words the model can act on.

Models do not agree on how to ask for a tool. The same prompt gets a fenced code block from one,
Anthropic-style XML from another, a Hermes `<tool_call>` from a third, and a ReAct
`Action:` / `Action Input:` pair from a fourth — and the same model will drift between them across
a run. MBPP-4 shortlists models on Groq and BENCH-3 compares prompts; both need the loop to keep
working when the format changes underneath. Rather than teach the sandbox four dialects, everything
that is not already Python is rewritten into a Python call string, and the sandbox stays
format-agnostic.

## Scope boundary

This module knows nothing about tools. It does not check that `read_file` exists, and it will
happily normalise a hallucinated tool name into a call. That call reaches the sandbox and comes
back a `NameError`, which is a correct and free diagnosis — the model reads it as an observation
and tries again. Validating names here would buy a slightly better message at the cost of a
dependency on MCP-2 (tool discovery), which is not built, and would move semantic control into a
module whose job is syntax.

Also out of scope: writing the observation string (CORE-4 owns the transcript), truncation and
compaction (CORE-7), and anything that calls an LLM. `extraction/` imports neither
`agent_smith.llm` nor `agent_smith.mcp`.

## Module layout

```
src/agent_smith/extraction/
├── __init__.py      # extract_code, ExtractionResult, Strategy
├── result.py        # ExtractionResult (frozen), Strategy
├── strategies.py    # the five candidate producers + STRATEGY_CHAIN
├── normalise.py     # a decoded tool call -> Python statements
├── repair.py        # the single repair attempt
└── extract.py       # extract_code(): the walk, the validation, the result
```

The split follows `config/` and `llm/`: one file per concern, module-level functions, private
helpers underscore-prefixed. `strategies.py` and `normalise.py` are separate because three of the
five strategies share the rendering and none of them owns it.

## The result

```python
class Strategy(str, Enum):
    FENCED = "fenced"
    XML = "xml"
    HERMES = "hermes"
    REACT = "react"
    BARE = "bare"


class ExtractionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str | None = None
    strategy: Strategy | None = None
    repaired: bool = False
    repair_note: str | None = None
    failure: str | None = None
```

`str, Enum` rather than `StrEnum`: `requires-python` is `>=3.10` and `StrEnum` landed in 3.11.

Frozen for the reason `LLMResponse` is frozen — this is a record of what happened, not state
anyone should edit afterwards. `code` is `None` if and only if `failure` is not; the two are never
both populated.

`strategy` does not follow that rule, deliberately. A strategy that matched its marker and then
failed to produce parseable code still names itself in the result, so a failure reads "the Hermes
block was there and its JSON would not decode" rather than "nothing worked". `strategy` is `None`
only when no marker matched at all — which is itself the useful distinction between a model that
formatted badly and a model that answered in prose.

`strategy` and `repaired` exist because the card asks for them, and they are worth more than
compliance: which format a model actually reaches for, and how often its output needs patching, is
exactly the kind of number BENCH-3 wants and would be tedious to reconstruct later.

## The chain

A module-level tuple, walked in order. Each strategy is a
`Callable[[str, int], Candidate | None]` — the reply and the step number — where `Candidate` holds
the code and an optional
`repair_note` for a repair the strategy performed while matching. A strategy has three outcomes,
not two, which is why it does not simply return a string:

- `None` — the marker was absent.
- a `Candidate` — the marker was there and produced code.
- `PayloadError` — the marker was there but its payload would not decode, its own repair attempt
  included. Raising rather than returning keeps the common path a plain value; the exception never
  leaves the module, because `extract_code` catches it.

The third outcome is what makes `strategy` nameable on a failure, and what gives payload-level
repair something to work on.

1. **`FENCED`** — a block opened by ` ``` ` with an optional `python` / `py` tag, closed by ` ``` `
   or by `<end_code>`. The first block wins: the CORE-6 prompt asks for one code block per turn,
   so in the nominal case "first" and "only" are the same block.
2. **`XML`** — `<invoke name="x"><parameter name="y">…</parameter></invoke>`.
3. **`HERMES`** — `<tool_call>{"name": …, "arguments": {…}}</tool_call>`, possibly several.
4. **`REACT`** — an `Action:` line naming the tool and an `Action Input:` line holding a JSON object.
5. **`BARE`** — last resort. The text must parse *and* the tree must hold at least one actionable
   node: `Call`, `Assign`, `AugAssign`, `AnnAssign`, `Import`, `ImportFrom`, `FunctionDef`,
   `AsyncFunctionDef`, `ClassDef`, `Return`. If the whole text does not parse, this strategy spends
   the repair itself before giving up: with no marker to delimit the code, "where does the Python
   start" is its own question, so `Here you go:` followed by a working line is a hit rather than a
   miss. The actionable-node test then still applies to the repaired text, which is what stops the
   repair from manufacturing code out of a prose reply by dropping lines until something parses.

Strategies 2 to 4 build their candidate through `normalise.py`. Strategies 1 and 5 hand back Python
the model already wrote.

`BARE` earns its place because "the model forgot the fence" is one of the most common malformations
in practice, and the four marker-based strategies all miss it. The actionable-node test is what
keeps it honest: prose almost never parses as Python, but a one-word reply like `Yes` parses
perfectly well as a `Name` expression, and without the second test it would be shipped to the
sandbox to earn a pointless `NameError`.

### First match engages

The strategy that matches owns the outcome. If its candidate does not parse, it gets the one repair
attempt; if that still fails, the extraction fails. The chain is not re-entered.

The four markers are distinctive enough that a text carrying two of them is rare, and when it
happens the higher one is almost certainly the payload. Falling through on a parse failure would
turn "first match wins" into "first parseable match wins" and would wreck the failure message: once
all five have been tried, there is no single reason left to tell the model, and a pile of
diagnostics is worse than one.

One consequence is counter-intuitive enough to state outright: **`BARE` does not rescue a broken
marker.** An unclosed fence that repair cannot mend fails, even when the full text would have
parsed. `BARE` is for "no marker at all", not for "a marker that went wrong".

## Normalisation

One assign-then-print pair per call, in order of appearance, at step 3:

```text
result_3_1 = read_file(filepath='/tmp/a.py', start_line=12, end_line=None)
print(result_3_1)
```

Two lines rather than one, because the two obvious single-line forms each lose something. A bare
`result_3_1 = read_file(...)` prints nothing, and the SBX-1 worker reports `stdout` and `stderr` —
so the model would fire a tool call and observe silence, learning nothing from its own action. A
bare `print(read_file(...))` shows the value but discards it, when a namespace that persists across
steps is the whole reason the sandbox works the way it does. Assign, then print: the value survives
into later steps, and `StepMetrics.sandbox_input` — archived in the submitted JSON and read by a
human marker — shows a named value and its display rather than a throwaway.

Several calls in one message get several pairs, `result_3_1`, `result_3_2`, and so on. Keeping only
the first would be precisely the silent truncation the subject calls out as a failure mode.

### Why the step is in the name

`extract_code(text, *, step)` takes the 1-indexed iteration number, and the name it produces is
`result_{step}_{index}`.

That is not decoration. `worker_main` builds the namespace once and hands the same dict to every
execution — persistence across steps is the point of SBX-1. A name restarting at `result_1` each
step would therefore overwrite the previous step's value in place, leaving a stale binding under a
live name with nothing to signal the substitution. The failure mode is silent and only bites the
model that trusts the transcript, which is the worst combination.

`step` is required rather than defaulted for the same reason. A default would make forgetting it
the easy path, and forgetting it restores exactly the bug.

### Rendering values

Argument values go through `repr()` applied to the JSON-decoded value, never through string
interpolation. Interpolation quotes everything, so `12` arrives as the string `"12"`, `null` as the
four-character string `"None"`, and a value containing a double quote produces a `SyntaxError`.
`repr()` gives a correct Python literal for every type JSON can produce — `str`, `int`, `float`,
`bool`, `None`, `list`, `dict` — and picks its own quoting, so `O'Brien` and `He said "hi"` both
come out valid. The guarantee holds *because* the input came from JSON; the `repr()` of an
arbitrary object would be `<Foo object at 0x…>`, which does not parse.

One leak has to be plugged for that to be true. Python's `json.loads` accepts `NaN`, `Infinity` and
`-Infinity` by default as an extension to the standard, and `repr(float("nan"))` is `nan` — a bare
name that resolves to nothing in the sandbox. Every `json.loads` call in this module therefore
passes a `parse_constant` that rejects them, which turns a would-be `NameError` three layers later
into an ordinary decode failure handled where it belongs.

XML needs one extra step. The format carries no type information — every `<parameter>` body is
text — so rendering `start_line` as `'12'` would hand TOOL-1 a string where it wants an `int`. Each
XML value is therefore passed to `json.loads`, and the decoded result is kept **only when it is not
a `str`**: `12` becomes `12`, `null` becomes `None`, `true` becomes `True`, while `/tmp/a.py` fails
to decode and stays the original string. The not-a-`str` condition is what stops an explicitly
quoted `"12"` from silently shedding its quotes.

## Repair

The card asks for one repair attempt on `SyntaxError`, reported back to the model. Read "one" as
"we do not loop with the LLM", not as "we try a single substitution": `repair_json(payload)` and
`repair_python(code)` each return `(repaired_text, note)` or `None`, walking a short ordered list of
fixes and keeping the first that makes the text valid. Two entry points rather than one because the
validity test differs — decoding for a payload, parsing for code — over a shared engine. From the
caller's side that is still one attempt, one `repaired=True`, one note.

Two of the fixes are worth naming, because the obvious version of each is wrong. Dropping prose
lines must reject a blank result: a candidate stripped down to nothing parses cleanly, so without
the guard the repair would turn a bad reply into an empty one. And closing an unterminated string
must close the open brackets in the same pass — a truncated `print("hello` needs its quote *and*
its parenthesis, and fixing either alone still will not parse.

The three malformations the card names do not live at the same level, and neither does the failure
they cause:

- **Unclosed fence** is a matching problem, not a parsing one — a fence that opens and never closes
  would otherwise simply fail to match. `FENCED` takes the rest of the text as the block and flags
  it as a repair.
- **Missing quote** most often breaks the *payload* of a marker before any Python exists: a
  `<tool_call>` whose JSON will not decode has matched but produced no candidate. Repair therefore
  runs on the payload text too, not only on assembled code — otherwise the commonest Hermes and
  ReAct malformation would be unreachable, since there is nothing yet for `ast.parse` to reject.
- **Stray prose** is parse-level, applied to a candidate that already exists.

Whichever level it ran at, it runs once, and a failure after it names the strategy that matched.

`repair_note` describes what was mended in one sentence. Reporting it is the point: a model that
learns its fence was unterminated stops producing unterminated fences, and a silent fix teaches it
nothing.

## Error surface

`extract_code` never raises. `json.loads`, `ast.parse` and every regex are guarded, and every path
returns an `ExtractionResult`. This is not defensive habit — CORE-4 carries a hard "must never
raise" obligation, since a crash scores as an automatic fail, and the cheapest way to honour it is
for the things it calls to hand back values instead of exceptions.

`failure` is written to be read by the model, so it names the formats that would have worked rather
than saying "extraction failed". CORE-4 decides how to wrap it into an observation.

## Testing

- **The corpus** — around 20 real, ugly LLM outputs, parametrised as
  `(text, expected strategy, expected code)`. This is the card's *done-when*. They live in a
  dedicated test module rather than as separate fixture files: the triples are easier to read and
  to grep when they sit together.
- **Per strategy** — each of the five tested on its own, without walking the chain.
- **Value rendering** — a table over `None`, `int`, `bool`, an apostrophe, a double quote, and a
  nested list; plus the XML `json.loads` step, including the `"12"` case that must stay a string.
- **Ordering** — a text carrying two markers goes to the higher strategy.
- **The step is in the name** — the same call rendered at step 1 and at step 3 produces different
  variables, so two steps cannot collide in the persistent namespace.
- **The `BARE` guard** — `Yes` fails instead of producing code.
- **`NaN` is not a value** — `{"x": NaN}` inside a `<tool_call>` fails to decode rather than
  rendering a bare `nan` into the call string.
- **Naming the failure** — a `<tool_call>` with undecodable JSON returns
  `strategy=HERMES, code=None`, while a plain prose answer returns `strategy=None`.
- **Never raises** — a corpus of junk (empty, binary, truncated JSON, a lone fence) returns a
  failure result and no exception.

## Deferred

Tool-name validation waits for MCP-2 and may never be worth it. Multi-strategy fall-through is
rejected above, not postponed. Truncating long extracted code is CORE-7's problem, not this
module's.
