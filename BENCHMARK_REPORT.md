# Model Benchmark Report

Five models against three SWE-bench tasks, run by `benchmarks/swe-matrix.sh`. The
backing `solution.json` files are under `benchmarks/runs-valid/`.

Read section 4 before section 2. The pass rate on its own overstates what was
measured, and the intermediary metrics are what say so.

## 1. Setup

| model | provider | why it is here |
| --- | --- | --- |
| `mistral-medium-latest` | Mistral | the reference a peer implementation reports 8/8 with |
| `devstral-medium-latest` | Mistral | Mistral's software-engineering family, the a-priori favourite |
| `codestral-2508` | Mistral | fastest of the three, expected weaker — a deliberate contrast |
| `magistral-small-latest` | Mistral | small reasoning model, cheap enough to be worth testing |
| `qwen/qwen3-235b-a22b-2507` | OpenRouter | a second provider, so the abstraction is shown not to be Mistral-shaped |

Tasks: `django__django-11066`, `sympy__sympy-14711`, `sympy__sympy-13480`.

They were chosen because all three are verified solvable by reference models and
arrive with the moulinette, so a failure is the agent's and not the task's. That
choice has a cost the report has to state: **all three are public commits in
repositories every one of these models was trained on.** Section 4 shows what that
does to the numbers.

Every run: 30 iterations, 300 000 input tokens, 10 000 output tokens, 900 s wall
clock — the SWE-bench ceilings of the subject (VI.1.2). Free tiers only.

## 2. Results

| model | task | pass | iters | input | output | s |
| --- | --- | --- | --- | --- | --- | --- |
| mistral-medium-latest | django-11066 | pass | 23 | 165 349 | 3 386 | 494 |
| mistral-medium-latest | sympy-14711 | fail | 30 | 235 494 | 2 028 | 680 |
| mistral-medium-latest | sympy-13480 | pass | 27 | 111 251 | 1 819 | 244 |
| devstral-medium-latest | django-11066 | fail | 16 | 166 070 | 10 000 | 107 |
| devstral-medium-latest | sympy-14711 | pass | 27 | 247 226 | 1 114 | 104 |
| devstral-medium-latest | sympy-13480 | pass | 3 | 4 672 | 562 | 70 |
| codestral-2508 | django-11066 | pass | 29 | 236 534 | 733 | 154 |
| codestral-2508 | sympy-14711 | fail | 30 | 168 129 | 7 673 | 126 |
| codestral-2508 | sympy-13480 | pass | 8 | 18 159 | 1 567 | 34 |
| magistral-small-latest | django-11066 | pass | 6 | 19 435 | 224 | 13 |
| magistral-small-latest | sympy-14711 | pass | 12 | 44 350 | 1 969 | 22 |
| magistral-small-latest | sympy-13480 | pass | 4 | 6 851 | 536 | 23 |
| qwen3-235b-a22b-2507 | django-11066 | pass | 4 | 11 109 | 896 | 33 |
| qwen3-235b-a22b-2507 | sympy-14711 | pass | 7 | 19 981 | 722 | 30 |
| qwen3-235b-a22b-2507 | sympy-13480 | pass | 4 | 6 343 | 566 | 38 |

Totals: **12 of 15**.

| model | solved | input | wall clock |
| --- | --- | --- | --- |
| qwen3-235b-a22b-2507 | 3/3 | 37 433 | 101 s |
| magistral-small-latest | 3/3 | 70 636 | 58 s |
| mistral-medium-latest | 2/3 | 512 094 | 1 418 s |
| devstral-medium-latest | 2/3 | 417 968 | 282 s |
| codestral-2508 | 2/3 | 422 822 | 313 s |

A pass means the task's own evaluation script accepted the patch, run in the
container with the test files restored from git. It does not mean the model
believed it had finished: see section 4.

The three failures are three different mechanisms, and none is a wrong patch.
`devstral` spent its entire 10 000-token output budget in sixteen turns and was
stopped with no budget left to submit. `mistral-medium` and `codestral` each
exhausted thirty iterations on `sympy-14711`.

## 3. Provider reliability

| model | requests | mean latency | retries | availability |
| --- | --- | --- | --- | --- |
| magistral-small-latest | 22 | 1.06 s | 0 | no failed run |
| codestral-2508 | 67 | 1.46 s | 0 | no failed run |
| mistral-medium-latest | 80 | 2.24 s | 40 | no failed run, heavily throttled |
| qwen3-235b-a22b-2507 | 15 | 4.06 s | 0 | no failed run |
| devstral-medium-latest | 46 | 5.00 s | 0 | no failed run |

**Retries concentrate entirely on the model that makes the most requests.**
`mistral-medium-latest` took 40 retries across 80 requests — one every other
request — while the other four took none. Mistral enforces its rate limits per
Workspace and per Organization rather than per key, so additional keys would not
have helped; what makes those 40 retries survivable is waiting the announced
delay out, within a retry budget sized from the benchmark's own wall clock.

No run was lost to a provider. Every failure in section 2 is a budget the agent
spent, not an endpoint that refused.

Latency is not a ranking. `devstral` is the slowest per request at 5.00 s, yet
finishes `sympy-13480` in 70 s, because it makes three requests. Requests made is
the variable that matters, and it is a property of the agent's efficiency rather
than of the endpoint.

## 4. Intermediary metrics

Two of the three the subject offers, and they are the part of this report that
carries information the pass rate does not.

### Exploration efficiency — the step of the first edit

| model | django-11066 | sympy-14711 | sympy-13480 |
| --- | --- | --- | --- |
| mistral-medium-latest | 15 | 26 | 1 |
| devstral-medium-latest | 1 | no `edit_file` | 1 |
| codestral-2508 | 2 | 2 | 1 |
| magistral-small-latest | 2 | 5 | 2 |
| qwen3-235b-a22b-2507 | 2 | 3 | 2 |

**Two runs out of fifteen ran the tests before editing anything.** The other
thirteen fixed the bug before reproducing it.

That number says less about the models than it looks. The task input carries a
`hints_text` field — the ticket's comment thread — and the prompt passes it
through. On two of these three tasks it contains the answer:

| task | `hints_text` | what it says | first edit |
| --- | --- | --- | --- |
| `django-11066` | 2 075 chars | "the fix can really be `using=db` in the `.save()` method" | step 1–3 |
| `sympy-13480` | 146 chars | "there is a typo on line 590: `cotm` should be `cothm`" | step 1–2 |
| `sympy-14711` | 0 chars | — | steps 3, 5 and 26 |

The one task that gives nothing away is the one where the five models behave
differently from each other. **A model that edits on turn 1 here has read the
fix, not remembered it**, and no metric computed over the first two rows measures
exploration.

Using the field is not a shortcut we chose: it is part of the task the evaluation
hands us, described in the contract as such, and dropping it would mean answering
a different question than the one asked. But it means `sympy-14711` is the only
one of our three tasks whose exploration numbers mean anything, and it is a single
run per model.

Any comparison against published SWE-bench figures is invalid for a second reason
too — our agent is given the task's evaluation script and can iterate against it,
which the canonical benchmark does not allow.

### Submission discipline — iterations between the tests passing and `final_answer`

Zero is ideal; one means the submission happens on the very next turn.

| model | django-11066 | sympy-14711 | sympy-13480 |
| --- | --- | --- | --- |
| qwen3-235b-a22b-2507 | 1 | 1 | 1 |
| magistral-small-latest | 1 | 1 | 1 |
| codestral-2508 | never saw a pass | never saw a pass | 2 |
| devstral-medium-latest | never saw a pass | never saw a pass | 1 |
| mistral-medium-latest | 7 | never saw a pass | never saw a pass |

`qwen` and `magistral` submit on the turn after the tests go green, on every task.
`mistral-medium` saw `django-11066` pass at step 16 and kept working until step 23
— seven iterations spent on a task already solved.

The "never saw a pass" cells are the ones worth pausing on. **Five runs submitted a
patch the agent had never watched succeed**, three of which the evaluation script
then accepted. Without the harness judging the submission, three of our twelve
passes would have been luck reported as competence.

## 5. Ablations

### The tool protocol

Same task (`django-11066`), same five models, same ceilings; three passes, changing
only the harness between them.

| pass | change | correct patch | accepted | silent turns | turns with no code |
| --- | --- | --- | --- | --- | --- |
| 1 | baseline | 3/5 | not verified | 13 | 8 |
| 2 | expression echo + comment-only feedback | 4/5 | 0/5 (see below) | 0 | 0 |
| 3 | + evaluation script run under bash | 5/5 | 4/5 | 0 | 0 |

**Pass 1 → 2.** A tool returns its result rather than printing it, so a turn that
forgot the `print()` executed, produced the output, and showed the model nothing.
Making the sandbox echo a bare expression the way a REPL does took silent turns
from 13 to 0, and took `devstral` from submitting nothing to producing the correct
patch. Feedback naming a comment-only block removed the eight turns that carried
no code.

Pass 2's zero acceptances are a defect in the measurement, kept here because it is
the ablation's most useful row: the validator ran the task's bash evaluation script
under `/bin/sh`, which is dash, which does not have `set -o pipefail`. Every
submission was refused on a script that died on its own second line.

**Pass 2 → 3.** Running that script under bash fixed it, and did more than repair
the validator: the script had never been executable through `run_tests` at all, for
the model either. `codestral-2508` had spent thirty iterations guessing argument
forms for a tool that could not have worked, and solves the task once it can run it.

The lesson generalises past this project. Every defect above was invisible in the
test suite, which was green throughout, and every one of them cost whole tasks.
They only appeared in the traces of real runs.

### The argument-error message

`devstral-medium-latest` is the one model that exhausted its output budget, so it
is the one this second ablation runs on. The trace named the cause, and it was not
verbosity: it called `edit_file` with an Aider-style `<<<<<<< SEARCH … >>>>>>>
REPLACE` block as a single positional argument, and what came back was an
eight-frame `inspect` traceback with the one useful sentence last. It then repeated
the identical call. The intervention is one line — bind positionally against the
published schema, and on a mismatch answer `Error: Invalid arguments for
'edit_file': missing a required argument: 'new_str'` instead of the traceback.

One task, one model, one run each; every other variable held.

| | baseline | readable error |
| --- | --- | --- |
| turns | 16 | 30 |
| turns carrying code | 14 | 22 |
| distinct code blocks | 8 | 22 |
| turns in `SEARCH/REPLACE` form | 6 | 0 |
| tracebacks returned | 4 | 1 |
| output tokens | 10 000, exhausted | 2 978 |
| stopped by | the output budget | the iteration ceiling |
| correct fix written | yes | no |
| task solved | no | no |

**It did what it targeted and nothing more.** The malformed form disappears, and
with it the loop: no code block is repeated in the second run, where the first
repeated six of fourteen. Output spend falls by 70 %, and the budget guard stops
being the binding constraint.

**And the run came out worse where it counts.** The baseline had written the
correct `db_manager(db)` fix and died holding it; the second run explores wider,
touches the right method twice, and never lands it. Neither run solved the task, so
the pass rate is 0/1 either way, but the patch regressed.

Two readings fit and one run cannot separate them: either the enforced turn budget
was doing accidental work by cutting off exploration near a patch devstral already
had, or the difference is the ordinary variance of one sample. What the ablation
does establish is narrower and still worth having — the harness defect was real,
it is gone, and devstral's failure on this task is now devstral's.

## 6. Conclusions

**Ship `magistral-small-latest`, with `qwen/qwen3-235b-a22b-2507` as the second.**

`magistral-small-latest` solves 3/3 in 58 seconds and 70 636 input tokens, has the
lowest latency of the five at 1.06 s, took no retries, and submits on the turn
after the tests pass on every task. It runs on Mistral, whose free tier throttles
requests per second without a daily token ceiling — the constraint that matters for
a benchmark run that has to be repeatable.

`qwen3-235b-a22b-2507` matches it at 3/3 for even fewer tokens (37 433, the lowest
of the five) and equals its submission discipline. It sits second only because it
runs on a second provider whose free quotas are the tighter of the two. Keeping it
configured is worth it regardless: it is what demonstrates the provider abstraction
holds outside Mistral.

**Discard `mistral-medium-latest`.** 2/3 for 512 094 input tokens and 1 418 seconds
— seven times the tokens and twenty-four times the wall clock of `magistral` for a
worse result — and 40 retries in 80 requests. It is also the only model that
explores before editing, which is genuinely the better methodology; on this
evidence that methodology costs more budget than it returns, and on `sympy-14711`
it explored until nothing was left to submit with.

**Discard `codestral-2508`.** 2/3, and the weakest submission discipline: it never
saw a passing test on two of three tasks.

**Hold `devstral-medium-latest`.** 2/3, and its one failure looked like a fixable
characteristic: it spent the whole 10 000-token output budget in sixteen turns, on
a task whose patch it had right. Section 5 measured it. The budget went to a
malformed tool call answered with a traceback, not to verbosity, and repairing that
removed the waste without making the task pass. The hold stands for a different
reason than it was written: what remains is a model that reaches for a tool format
we do not accept, at a token cost the other four do not pay.

### What this report does not establish

Three tasks and one run each. No result here separates two models whose scores
differ by one task.

Two of the three tasks ship a `hints_text` that names the fix, so thirteen of
fifteen runs edited before reproducing anything. What is measured on those two is
closer to "can follow a diagnosis and drive the tools" than to "can debug an
unfamiliar repository". A task with no hints — and outside the training data of
all five models — would measure the
second, and we do not have one.

---

*Runs produced by `benchmarks/swe-matrix.sh`; backing files under
`benchmarks/runs-valid/`.*
