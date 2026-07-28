# Agent Smith — Scope, Architecture & Kanban Breakdown

> Working document for a 3-person team. **Provider is not ours to hardcode:** the model name and
> provider URL arrive as CLI flags (`--model-name` / `--provider-url`) and the `.env` is supplied at
> run time, so the provider must be **derived from the URL**. Groq is only our *development* default,
> applied when nothing is passed; OpenRouter is kept configured and tested as the fallback (the
> subject's own example key is `OPENROUTER_API_KEY`).
>
> _Revised after auditing the plan against the subject (v1.1) and the moulinette source. See_
> `AUDIT_PLAN_VS_SUBJECT.md` _for the reasoning behind each change._

---

## 1. What we are actually building

An **autonomous code agent**: a loop that asks an LLM for Python code, executes that code in a
locked-down sandbox, feeds the result back, and repeats until the model calls `final_answer()`.

The code the LLM writes calls **tools** (read files, grep, edit, run tests), and those tools are
served by **our own MCP server(s)**. Two benchmarks are targeted:

| Benchmark | Task | Pass bar |
|---|---|---|
| MBPP | Write a Python function that passes hidden tests | 4 / 5 random tasks |
| SWE-bench Verified | Fix a real bug in a real repo inside Docker, output a git patch | 2 / 3 random tasks |

Plus a third exam on the **sandbox itself** (import block, builtin block, network block, path
restriction, timeout, memory limit, MCP protocol) which must pass **100%**.

### Deliverables checklist

- [ ] `agent_mbpp` and `agent_swebench` CLI modules with the exact flags from the subject
- [ ] `sandbox` CLI (interactive REPL mode + config file + `--mcp-stdio` / `--mcp-server`)
- [ ] `mcp_tools_mbpp.py` and `mcp_tools_swebench.py` **at repo root**
- [ ] All 9 mandatory tools, individually testable, working without the agent loop
- [ ] `BENCHMARK_REPORT.md` — 5 models × 3 SWE-bench tasks + ablation
- [ ] Backing `solution.json` files committed
- [ ] `README.md` in English, first line italicised `_This project has been created as part of the 42 curriculum by <login1>, <login2>, <login3>._`
- [ ] `sandbox_template.json` + model config files
- [ ] Python 3.10, `uv`, no `smolagents` / `langgraph` / `crewai` / `autogen` / `llama-index`, no `RestrictedPython`

---

## 2. The constraints that should drive every design decision

These are the numbers that will kill us if we design first and read them later.

### MBPP — brutally tight

| Metric | Limit |
|---|---|
| Iterations | 10 |
| **Input tokens (cumulative)** | **6 000** |
| **Output tokens (cumulative)** | **1 500** |
| Wall-clock | 120 s |

The input budget is cumulative *across the whole task*. Conversation history is re-sent every
turn, so cost grows quadratically. With a 900-token system prompt:

```
iter 1: 950      total   950
iter 2: 1 350    total 2 300
iter 3: 1 800    total 4 100
iter 4: 2 300    total 6 400   ← BUDGET BLOWN
```

**Consequence:** the MBPP agent has to solve in **2–3 iterations**, and the MBPP system prompt
must be **≤ 800 tokens**. We need a *separate, minimal* prompt for MBPP — not the SWE-bench one.
Also 1 500 output tokens total means no reasoning model on MBPP (thinking tokens count).

### SWE-bench — roomy but needs hygiene

| Metric | Limit |
|---|---|
| Iterations | 30 |
| Input tokens (cumulative) | 300 000 |
| Output tokens (cumulative) | 10 000 |
| Wall-clock | 900 s |

300k / 30 iterations ≈ 10k average input per call. A naive append-only history blows this by
iteration ~15 on any real repo. We need **observation truncation + history compaction** from day one.
10k output over 30 steps = ~330 tokens per step, so the prompt must discourage the model from
re-printing large blobs of code.

---

## 3. Architecture

```
                     ┌────────────────────────── agent process ──────────────────────────┐
   Groq / OpenRouter │                                                                    │
        ▲            │  ┌───────────┐   prompt    ┌──────────────┐   code   ┌───────────┐ │
        │            │  │ LLMClient │◄───────────►│ Orchestrator │─────────►│ Extractor │ │
        └────────────┼──┤ (rotation │             │  (the loop)  │◄─────────┤ (4 fmts)  │ │
                     │  │ + retries)│             └──────┬───────┘  obs     └───────────┘ │
                     │  └───────────┘                    │                                │
                     │                                   │ exec(code)                     │
                     │                            ┌──────▼────────┐   RPC   ┌───────────┐ │
                     │                            │ Sandbox proxy │◄───────►│ MCP client│ │
                     │                            └──────┬────────┘         └─────┬─────┘ │
                     └───────────────────────────────────┼────────────────────────┼───────┘
                                                         │ pipe                   │ stdio / HTTP
                                              ┌──────────▼──────────┐   ┌─────────▼─────────┐
                                              │  sandbox worker     │   │    MCP server     │
                                              │  (separate process) │   │ (our tools)       │
                                              │  restricted import  │   └─────────┬─────────┘
                                              │  audit hook, rlimit │             │ docker exec
                                              │  persistent globals │   ┌─────────▼─────────┐
                                              │  final_answer()     │   │  /testbed in       │
                                              └─────────────────────┘   │  Docker container  │
                                                                        └────────────────────┘
```

**Key design call:** the sandbox worker has **no network** (that's a hard requirement), so it
cannot hold the MCP client socket itself. Tool wrappers injected into the sandbox namespace are
thin stubs that serialise `(tool_name, kwargs)` over the pipe to the parent, which owns the real
MCP client and performs the call. The subject's diagram draws the MCP client "inside" the sandbox;
functionally we still satisfy it — LLM code calls tool functions from the sandbox namespace, and
tool actions happen outside the sandbox security domain, which is exactly what the subject says on
p.17. Worth a sentence in the README so the evaluator doesn't think we misread it.

### Proposed repo layout

```
.
├── mcp_tools_mbpp.py            # required at root
├── mcp_tools_swebench.py        # required at root
├── sandbox_template.json
├── models.json
├── BENCHMARK_REPORT.md
├── README.md
├── pyproject.toml               # uv, python 3.10, [project.scripts] sandbox = ...
└── src/
    ├── agent_mbpp/__main__.py       # thin top-level pkg: delegates to agent_smith.cli.mbpp
    ├── agent_swebench/__main__.py   # thin top-level pkg: delegates to agent_smith.cli.swebench
    └── agent_smith/
        ├── models/          # Pydantic: SandboxConfig, MBPPTaskInput, SWEBenchTaskInput,
        │                    #           StepMetrics, SolutionOutput
        ├── llm/             # provider abstraction, key rotation, retry, usage tracking
        ├── extraction/      # multi-format code extraction + normalisation
        ├── sandbox/         # worker process, guards, RPC protocol, REPL, manual generator
        ├── mcp/             # client (stdio + streamable HTTP), tool discovery, wrapper codegen
        ├── agent/           # orchestrator, prompts/, history compaction, budget guard
        ├── tools/           # tool implementations, shared by both MCP servers
        ├── docker/          # container lifecycle for SWE-bench
        └── cli/             # mbpp, swebench, sandbox entrypoints
```

> **Why the two thin top-level packages:** the subject invokes the agents as `python -m agent_mbpp`
> / `python -m agent_swebench`, which requires importable **top-level** modules of those exact names
> on `sys.path`. Burying them under `agent_smith.cli` would fail with `No module named agent_mbpp`.
> `pyproject.toml` must ship all three packages in the wheel.

> **Tools run against a configurable root, not "Docker" hardwired.** Each tool
> (`read_file`, `search_code`, `edit_file`, `run_tests`, `get_patch`, …) resolves paths under a
> configurable testbed root (`TESTBED_PATH`, default `/testbed`). That root can be a plain directory
> on disk *or* a path inside a Docker container reached via `docker exec`. This is what lets the 9
> tools be exercised **standalone, without the agent loop and without Docker** (a deliverable in its
> own right) — the diagram above shows the SWE-bench production path, not the only path.

---

## 4. Team split

Three parallel workstreams that touch each other only through Pydantic models and one RPC protocol.
Freeze both **on day one** (task `SETUP-2`) — everything else can then move independently.

| Stream | Owner | Scope |
|---|---|---|
| **A — Sandbox** | Dev 1 | Sandbox worker, security guards, REPL, manual generation, sandbox exam |
| **B — Agent** | Dev 2 | LLM abstraction, Groq, key rotation, extraction, orchestrator, prompts, metrics |
| **C — Tools** | Dev 3 | MCP server + client, the 9 tools, Docker lifecycle, SWE-bench plumbing |

Integration points where two people must sit together: `SBX-4` (tool wrappers), `MBPP-1`,
`SWE-1`. Benchmark report (`BENCH-*`) is shared — one person runs the matrix, all three write analysis.

> **Docker is a day-one dependency on all three machines, not just Stream C.** MBPP validation runs
> the generated solutions inside a `python:3.11-slim` container (`mbpp/interact.py`), so Dev 1 and
> Dev 2 need a working Docker daemon to validate *anything* in MBPP — it is not only a SWE-bench
> concern. Add "Docker runs" to each machine's `SETUP` checklist.

---

## 5. Kanban board

**Columns:** `Backlog` → `Ready` → `In Progress` (WIP limit **2 per person**) → `Review` → `Blocked` → `Done`

**Definition of Ready:** acceptance criteria written, dependencies in `Done`, owner assigned.

**Definition of Done:** merged to `main`, has at least one test or a documented manual repro,
docstrings written, doesn't break `exam_sandbox.sh`.

**Card format:** `ID · title · owner · size (S/M/L) · depends-on`

### Milestones

| # | Goal | Contains |
|---|---|---|
| **M0** | Skeleton compiles, contracts frozen | SETUP-1..4 |
| **M1** | One MBPP task solved end-to-end, limits off | CORE-1..5, SBX-1..2, MCP-1..3, MBPP-1..2 |
| **M2** | MBPP exam passes 4/5 within limits | MBPP-3..5, CORE-6..7 |
| **M3** | Sandbox exam passes 100% | SBX-3..8 |
| **M4** | One SWE-bench task solved | SWE-1..6, TOOL-1..9 |
| **M5** | SWE exam 2/3 + benchmark report + README | SWE-7..9, BENCH-1..4, DOC-1..3 |

---

## 6. Task cards

### Epic SETUP — foundations

---

**`SETUP-1` · Repo skeleton + uv + tooling** · Dev 1 · S · —

`pyproject.toml` pinned to Python 3.10, `uv` lockfile, `[project.scripts] sandbox = "agent_smith.cli.sandbox:main"`,
ruff + pytest. ~~pre-commit hook blocking anything that looks like an API key (regex on `gsk_`,
`sk-`)~~ — **dropped from this card, 2026-07-28.** The subject asks for no such hook, and the graded
criterion it was meant to serve (VI.3: *"any API key found in your source code will be flagged
during evaluation"*) is about the source we ship, which `SETUP-3` satisfies by loading keys from the
environment. A git hook guards a different thing — our own commits — and `--no-verify` makes it
advisory anyway. Nothing enforces the secrets rule today and `CONTRIBUTING.md` now says so plainly
instead of promising a hook that never existed. Reinstate here if we decide we want the net.
Declare the two thin top-level packages `agent_mbpp` and `agent_swebench` in the wheel so the
subject's `python -m …` invocation resolves (see §3).

*Done when:* on all three machines, `uv run sandbox --help`, `uv run python -m agent_mbpp --help`
and `uv run python -m agent_swebench --help` all print usage (the `-m` forms are the ones the
subject actually calls — test them explicitly, not just the `sandbox` script).

---

**`SETUP-2` · Freeze Pydantic contracts** · all three, 1h together · S · SETUP-1

**Copy `moulinette/models_public.py` verbatim** into `src/agent_smith/models/contract.py` (its own
header says students are meant to copy it; it is the exact file the moulinette imports to validate
our output). Do **not** retype the models — a single renamed field fails
`SolutionOutput.model_validate()` before any test runs. Derive our internal ones
(`ToolSpec`, `ExecutionResult`, `LLMResponse`, `AgentConfig`) alongside it, never on top of it.

Two field-level traps to encode as tests, not code-review notes:

- **`SolutionOutput.task_id` is a `str`** even for MBPP, whose `task_id` is an `int`. Pydantic v2
  will not coerce `int → str`; writing `"task_id": 282` is rejected as `string_type` and the task is
  scored failed before correctness is even checked. Always emit `str(task.task_id)`.
- **`SolutionOutput` carries the full `system_prompt`** and each `StepMetrics` carries `llm_output`,
  `sandbox_input`, `sandbox_output`, `model_name`, `api_url`, `retries` and per-step token counts.
  These are how the run is audited for integrity — wire them to the real execution path from the
  start (see `DOC-3`), never post-hoc.

*Done when:* `models/` is merged, a round-trip test serialises/validates a sample `solution.json`
through the moulinette's own model, and nobody edits it again without telling the other two.

---

**`SETUP-3` · Config files + env loading** · Dev 2 · S · SETUP-2

`sandbox_template.json` (matches `SandboxConfig` defaults), `models.json` (provider → base_url,
model list, per-model stop sequences, max_tokens). `.env` loading via `python-dotenv`.

**Key resolution must be provider-agnostic.** We do not control the `.env` handed to us at run time,
nor the key names in it. Resolve the provider from `--provider-url` (not a hardcoded constant), then
scan several conventions for that provider: `<PROVIDER>_API_KEY`, `<PROVIDER>_API_KEY_2`, `…_N`,
`<PROVIDER>_API_KEYS` (comma-separated), and a generic fallback. Groq env names apply only as the
dev default when nothing else is passed; `OPENROUTER_API_KEY` must work out of the box (it is the
subject's own example). `.env.example` committed, `.env` gitignored.

*Done when:* the same code path loads keys for a Groq URL and an OpenRouter URL with no source
change, and zero secrets are in git history (`git log -p | grep -E 'gsk_|sk-'` is empty).

---

**`SETUP-4` · Task fixtures & dev harness** · Dev 3 · S · SETUP-2

Dump 3 MBPP tasks + the 3 suggested SWE tasks (`sympy__sympy-14711`, `sympy__sympy-13480`,
`pydata__xarray-4629`) into `tests/fixtures/`. A `make dev-mbpp` / `make dev-swe` shortcut so nobody
retypes the 4-line CLI invocation forty times a day.

---

### Epic CORE — LLM layer & agent loop (Stream B)

---

**`CORE-1` · Provider abstraction** · Dev 2 · M · SETUP-3

Abstract `LLMProvider` with one method: `complete(messages, stop, max_tokens) -> LLMResponse`
carrying `text, input_tokens, output_tokens, latency_ms, model, api_url, retries`.
`OpenAICompatProvider` implements it with plain `httpx` (not the `openai` SDK — one less dependency
and we control retry semantics). The concrete endpoint is **selected from `--provider-url`**, never
a hardcoded constant: Groq is `https://api.groq.com/openai/v1`, OpenRouter (and any other
OpenAI-compatible free-tier endpoint) then comes for free by changing the base URL.

> Model catalogues change often — read `GET /openai/v1/models` at startup and validate the
> configured name against it rather than hardcoding a list that rots.

> The lone `httpx` dependency is for the LLM call itself and nothing else. Because a blanket grep for
> `httpx`/`urllib`/`requests` is a standard anti-cheat signal (it *could* be used to fetch
> solutions), say in one README line that our only outbound HTTP is the inference call, so the
> legitimate use isn't mistaken for exfiltration.

---

**`CORE-2` · API key rotation + retry policy** · Dev 2 · M · CORE-1

Mandatory per the subject. A `KeyPool` holding N keys per provider with round-robin plus
cooldown: on HTTP 429 read `retry-after` / `x-ratelimit-reset-*` headers, park that key until the
reset timestamp, move to the next. Exponential backoff with jitter on 5xx and timeouts. Every
retry increments `StepMetrics.retries`. Cap total retries so we don't eat the 120 s MBPP wall-clock.

*Watch out:* Groq free tier limits are **per-day tokens as well as per-minute** — a key can be dead
for hours, not seconds. Park-until-tomorrow must be a state the pool understands, otherwise we'll
spin on a dead key during the exam.

---

**`CORE-3` · Multi-format code extraction** · Dev 2 · M · SETUP-2

Ordered strategy chain, first match wins:

1. ` ```python … ``` ` / `<end_code>` fenced block
2. Anthropic-style XML `<invoke name="x"><parameter name="y">…</parameter></invoke>`
3. Hermes `<tool_call>{"name": …, "arguments": {…}}</tool_call>`
4. ReAct `Action: tool / Action Input: {…}`

Everything non-Python is **normalised into a Python call string** (`result = read_file(filepath="…")`)
so the sandbox stays format-agnostic. Validate with `ast.parse` before sending; on `SyntaxError`
attempt one repair (unclosed fence, stray prose, missing quote) and — critically — **report the
repair back to the LLM** as part of the observation. Return an `ExtractionResult` that says which
strategy matched and whether it was repaired.

*Done when:* a parametrised test suite of ~20 real ugly LLM outputs all extract correctly.

---

**`CORE-4` · Orchestrator loop** · Dev 2 · L · CORE-1, CORE-3, SBX-1

The `Thought → Code → Observation` loop. Responsibilities: build messages, call LLM, extract,
execute, append observation, check `final_answer`, check budgets, record `StepMetrics`, emit
`SolutionOutput`. Configurable `max_iterations`. Must never raise — every failure path produces a
valid `SolutionOutput` with `success=false` and a populated `error` (a crash = automatic fail).

Handle: KeyboardInterrupt/SystemExit propagating cleanly, and a `finally` that always writes the
output JSON and cleans up containers.

---

**`CORE-5` · Budget guard** · Dev 2 · S · CORE-4

Runs before each LLM call: estimate the request size, and if `total_input + estimate > limit`,
stop and force a submission attempt rather than blowing the limit (an over-budget run is scored as
a failure — better to submit whatever we have). Same for wall-clock, with a safety margin of ~15 %.

---

**`CORE-6` · Prompt engineering: two prompts** · Dev 2 + whoever · L · CORE-4

`prompts/mbpp.md` (**≤ 800 tokens**, hard rule) and `prompts/swebench.md`. Both contain: tool
documentation injected from the generated sandbox manual, the `Thought/Code/Observation` format
with one worked example, and an explicit methodology. For SWE-bench the methodology should mirror
how *we* solved the task by hand: reproduce → locate with `search_code` → read narrow line ranges →
edit minimally → `run_tests` → `final_answer(get_patch())`.

Stop sequences configured per model: `["<end_code>", "</tool_call>", "Observation:"]`.

**MBPP prompt must forbid over-fitting to the visible asserts.** The task harness hides the first
test from the agent (`InteractMBPP.get_task()` serves `test_list[1:]` / `test_imports[1:]`) but
validation runs on the *complete* list. So an agent that hardcodes the values of the asserts it can
see passes locally and fails validation. The prompt must demand a **general** implementation and
must state that the solution has to carry **its own `import` statements** — the sliced
`test_imports` means the agent cannot rely on imports provided by the test harness.

*Note:* the subject explicitly invites a vague-vs-explicit prompt comparison — capture the numbers,
it's a free ablation for `BENCH-3`.

---

**`CORE-7` · History compaction** · Dev 2 · M · CORE-4, CORE-5 · *blocks `MBPP-3`*

Keep system + task + first 2 steps + last N steps verbatim, replace the middle with
a one-line summary per elided step (`step 7: read /testbed/sympy/core/mul.py:100-160`). Truncate
every observation to K chars with an explicit `[... truncated, 4210 chars omitted ...]` marker —
silent truncation is called out in the subject as a failure mode.

**This is needed for MBPP too, not just SWE-bench.** The quadratic-history math in §2 (6 400 input
tokens by iteration 4) assumes an append-only transcript; the "solve in 2–3 iterations" conclusion
only treats the symptom. A sliding window that resends system + task + last observation keeps
per-iteration input roughly flat and makes the full 10 iterations actually usable. Therefore `CORE-7`
is a **dependency of `MBPP-3`**, not a SWE-bench-only task.

---

### Epic SBX — sandbox (Stream A)

---

**`SBX-1` · Worker process + persistent namespace** · Dev 1 · L · SETUP-2

Sandbox runs in a **separate process** (`multiprocessing`, `spawn` start method) with a
long-lived `globals()` dict so variables persist between steps — that's the whole point of code-based
tool calling. Parent ↔ worker over a `Pipe`: `{"exec": code}` in, `{"stdout","stderr","error","final_answer"}` out.
stdout/stderr captured via `contextlib.redirect_stdout`.

*Design note:* keep two timeout layers. A **soft** in-worker `signal.setitimer` raising
`TimeoutError` (namespace survives, partial output is preserved and reported) and a **hard**
parent-side `join(timeout)` → `SIGKILL` → respawn if the worker went unresponsive. The subject
requires reporting "execution hit the timeout and output is partial", which only the soft layer can do.

---

**`SBX-2` · `final_answer()`** · Dev 1 · S · SBX-1

Injected into the namespace, not an MCP tool. Raises a private `_FinalAnswer(value)` exception
caught at the worker's exec boundary and reported to the parent. Available regardless of which MCP
server is connected.

---

**`SBX-3` · Import allowlist** · Dev 1 · M · SBX-1

Replace `builtins.__import__` with a guard checking against `authorized_imports`, honouring the
`"math.*"` wildcard form. Pre-purge `sys.modules` of anything not on the list so `import` can't be
dodged by grabbing an already-loaded module, and block the classic escapes:
`__builtins__`, `__loader__`, `importlib`, `__class__.__mro__` walks.

---

**`SBX-4` · Restricted builtins + audit hook** · Dev 1 · M · SBX-3

Two layers, because a builtins allowlist alone is not enough:

- Drop/override `eval`, `exec`, `compile`, `open`, `input`, `__import__`, `globals`, `breakpoint`.
- `sys.addaudithook()` (stdlib, 3.8+) intercepting `open`, `os.system`, `subprocess.Popen`,
  `socket.socket`, `socket.connect`, `os.exec*` — this is the single cleanest way to enforce both
  path restriction and the network ban without shipping a whole seccomp setup. The hook resolves
  paths with `os.path.realpath` and checks containment in `allowed_directories` (defeats
  `../` and symlink escapes).

**Handle an authorised directory that doesn't exist on this host.** The default `allowed_directories`
are `/testbed` and `/tmp/agent`; when the sandbox runs on the host (not in the SWE-bench container)
`/testbed` may not exist. The path-restriction logic must treat "authorised but absent" as a clean,
readable denial for anything outside the set — never a crash — and the sandbox must **create
`/tmp/agent` at startup** so scratch writes have a legal home.

*Done when:* a red-team test file with ~15 escape attempts is fully blocked, and a path check
against an authorised-but-missing directory returns a clear message instead of an exception.

---

**`SBX-5` · Memory limit + no network** · Dev 1 · S · SBX-4

`resource.setrlimit(RLIMIT_AS, max_memory_mb)` in the worker after fork. Network: audit hook above
plus a `socket.socket` override raising `PermissionError`. Every denial must return a *clear*
message to the LLM ("network access is disabled in this sandbox"), not a bare traceback.

---

**`SBX-6` · Sandbox CLI + interactive REPL** · Dev 1 · M · SBX-1, MCP-2

All four invocation forms from the subject. REPL: prompt, read a block, execute under the same
restrictions, print result or error, loop; clean exit on `exit` or Ctrl-D. Multi-line input handling
(keep reading until the block parses or a blank line).

---

**`SBX-7` · Structured feedback messages** · Dev 1 · M · SBX-1, CORE-3

The five mandated cases, each with its own message template: no code block found; malformed but
repaired (say *how*); timeout with partial output; output truncated (say by how much); edit
introduced a syntax/lint error. Centralise them in one module so wording stays consistent.

---

**`SBX-8` · Dynamic sandbox manual generator** · Dev 1 · M · MCP-2

Read tool schemas from `list_tools()` and render markdown: signature, description, parameter types,
one usage example per tool. Regenerated at every connection so an **unknown MCP server** (which the
evaluators will plug in) is documented automatically. This string is what gets injected into the
system prompt — keep it compact, it's paid for on every iteration.

---

### Epic MCP — protocol layer (Stream C)

---

**`MCP-1` · MCP client, both transports** · Dev 3 · M · SETUP-2

Official `mcp` Python SDK (a protocol library, not an orchestration framework — allowed).
`stdio_client` for `--mcp-stdio "python mcp_tools_swebench.py"`, `streamablehttp_client` for
`--mcp-server <URL>`. Same interface behind both. Reconnect on transport drop; a dead MCP server
must surface as a readable observation, not a crash.

---

**`MCP-2` · Tool discovery → callable wrappers** · Dev 3 + Dev 1 · M · MCP-1, SBX-1

Turn each discovered tool into a Python function in the sandbox namespace with the right name,
signature and docstring (build from the JSON schema; generate source with `exec` in the *parent*,
or bind a `functools.partial` with `__signature__` set). Calling it sends an RPC over the pipe.
Argument validation happens before the RPC so bad calls give an immediate, useful error.

---

**`MCP-3` · MBPP MCP server** · Dev 3 · M · MCP-1

`mcp_tools_mbpp.py` with FastMCP. `run_tests(code)` → writes to a temp dir, runs
`test_imports + test_list` assertions in a subprocess with a timeout, returns pass/fail per test
with the assertion text on failure. Also expose MCP **resources** and **prompts** (explicitly
required, easy to forget).

---

**`MCP-4` · SWE-bench MCP server** · Dev 3 · L · MCP-1, TOOL-*

`mcp_tools_swebench.py` exposing the 9 mandatory tools. They operate against the configurable
testbed root from §3 (`TESTBED_PATH`), which must work **both** as a plain directory on disk **and**
as a path inside the Docker container. The standalone filesystem mode is not optional: the tools are
exercised directly against a local git repo — no agent loop, no Docker — so `docker exec` must never
be wired in as the only code path. `get_patch()` in that mode is a `git diff` in the testbed dir.

Only our **own** MCP servers are ever connected — never a public or third-party MCP server, for any
purpose including testing.

---

### Epic TOOL — the 9 mandatory tools (Stream C)

Each is a small card; they are graded **individually**, so each needs its own test. Output formats
are specified in the subject and must be matched character-for-character.

| ID | Tool | Notes |
|---|---|---|
| `TOOL-1` | `read_file(filepath, start_line, end_line)` | `cat -n` style `N: content`. Clamp ranges, don't crash on out-of-bounds. Default to a window, never the whole file. |
| `TOOL-2` | `edit_file(filepath, old_str, new_str)` | Exact-string replace. **Fail loudly** if `old_str` occurs 0 or ≥2 times — this is the #1 silent agent failure. Post-edit `ast.parse` (or `ruff --quiet`) and report syntax breakage per `SBX-7`. |
| `TOOL-3` | `list_files(directory, pattern)` | glob/fnmatch, cap the result count with a truncation notice. |
| `TOOL-4` | `search_code(pattern, file_pattern)` | `/abs/path.py:<line> <content>`. `grep -rn` inside the container is fastest; fall back to `ripgrep` if we install it. Cap matches. |
| `TOOL-5` | `search_function_or_class_definition_in_code(name)` | Walk `ast` for `FunctionDef`/`AsyncFunctionDef`/`ClassDef`; more precise than grep and immune to comments. |
| `TOOL-6` | `find_references(name, filepath, line)` | `jedi` in the container gives real references; keep an AST/grep fallback so the tool never hard-fails. |
| `TOOL-7` | `run_tests()` | Runs the task's `eval_script`. **Parse the output** into `N passed / M failed` + failing test names — the raw pytest dump will eat the token budget. |
| `TOOL-8` | `get_patch()` | `git -c core.fileMode=false diff` exactly as specified, **restricted to tracked files**. The real failure mode isn't `.pyc` — it's a `reproduce.py` the agent drops in `/testbed` to reproduce the bug, which lands in the diff and can break the eval script's pytest collection. Force all scratch/reproduction files into `/tmp/agent` (see prompt + `SBX-4`) and keep the patch to tracked sources. |
| `TOOL-9` | `run_command(command, workdir)` | stdout + stderr + exit code, with a timeout and truncation. |

---

### Epic MBPP — benchmark 1 (integration)

---

**`MBPP-1` · `agent_mbpp` CLI** · Dev 2 · M · CORE-4, MCP-3

Exact flags: `--task-file --output --model-name --provider-url`. Loads `MBPPTaskInput`, runs the
loop, writes `SolutionOutput`. Never exits non-zero without having written the JSON.

---

**`MBPP-2` · First green task, limits disabled** · Dev 2 · M · MBPP-1

Prove the pipeline works before optimising anything. Success = one task validated by the moulinette.

---

**`MBPP-3` · Fit inside 6k/1.5k/10** · Dev 2 · M · MBPP-2, CORE-5, CORE-6, CORE-7

Shrink the prompt, force `run_tests` then `final_answer` in as few turns as possible, and rely on
the sliding history from `CORE-7` to keep per-iteration input flat. Measure actual token spend per
iteration and put the table in the README.

---

**`MBPP-4` · Model shortlist on Groq** · Dev 2 · S · MBPP-3

Test 3–4 non-reasoning Groq models on 10 MBPP tasks, pick the default. Reasoning models are
disqualified here by the 1 500-token output cap.

---

**`MBPP-5` · 20-task regression run** · Dev 2 · M · MBPP-4

We need well above 80 % locally to survive a 4/5 exam with no retries. Log failures by category
(extraction miss, wrong logic, budget overrun) — that's what tells us where to spend effort.

Efficiency counts beyond the pass bar: quality is judged on how far *under* the limits we run (few
iterations, low token spend), not just pass/fail. Track avg iterations and % of budget used as
first-class metrics and treat "5/5 with low iteration counts" as the real target, not "4/5 anyhow".

---

### Epic SWE — benchmark 2 (integration)

---

**`SWE-1` · Docker lifecycle manager** · Dev 3 · L · SETUP-4

Pull image, start container, mount/locate `${TESTBED_PATH}`, exec commands, **guaranteed cleanup**
(context manager + `try/finally` + `atexit` + a `docker rm -f` sweep on startup for orphans from
crashed runs). Cleanup is explicitly graded.

---

**`SWE-2` · Container-side dependency bootstrap** · Dev 3 · S · SWE-1

Install `ruff`, `jedi`, `ripgrep` inside the container once at start, tolerate failure gracefully
(no network in some images → tools must degrade to fallbacks, not die).

---

**`SWE-3` · `agent_swebench` CLI** · Dev 2 + Dev 3 · M · CORE-4, MCP-4, SWE-1

Same CLI shape as MBPP. Writes `solution = get_patch()` into `SolutionOutput`.

---

**`SWE-4` · Solve `sympy__sympy-14711` by hand** · anyone · S · SWE-1

Do the task manually using only our own tools. **The transcript of that session is the SWE-bench
system prompt.** The subject says this twice; it's the highest-leverage hour in the whole project.

---

**`SWE-5` · First green SWE task** · all · L · SWE-3, SWE-4, TOOL-*

---

**`SWE-6` · Iteration/token discipline** · Dev 2 · M · SWE-5, CORE-7

Instrument: where does the budget actually go? Usually oversized `read_file` windows and raw
pytest output. Fix the tools, not the prompt.

---

**`SWE-7` · Generalisation check** · all · L · SWE-6

Run 8–10 varied SWE-bench Verified tasks (not just the 3 suggested ones) to confirm we haven't
overfitted. The exam draws 3 at random from the full set. As with MBPP, aim well under the 30-iter /
300k-token ceilings — efficient convergence (low iterations, small gap between "tests pass" and
`final_answer`) is what separates a solid run from a minimum pass.

---

**`SWE-8` · Failure-mode hardening** · all · M · SWE-7

Model loops on the same edit, `edit_file` never matches, tests time out, patch is empty at
`final_answer`. Each needs a detection + a nudge back into the observation stream.

---

**`SWE-9` · Anti-cheat policy** · all · S · SWE-3

Provenance is graded hard: fetching a fix from PRs/issues/external sources, or applying a memorised
patch without real exploration, is scored **0** — not a failed task. Three decisions to make and
document explicitly rather than leave to chance:

- **Container network: off by default.** With no network, a `run_command("pip install …")` or a
  `curl github.com` simply can't happen — consistent with `SWE-2`'s "tolerate failure" bootstrap.
  Decide it, don't inherit it by accident.
- **`run_command` scope in `/testbed`:** whether `git log` / `git show` are allowed at all. Pin the
  policy and document the reasoning.
- **Prompt wording:** neither system prompt may invite the model to "recall" an upstream fix. The
  methodology must read as explore-then-fix, because `llm_output` / `sandbox_input` are read back to
  trace how the agent actually reasoned — a further reason never to truncate those two fields.

---

**`BENCH-1` · Benchmark runner script** · Dev 2 · M · SWE-5

Runs the model × task matrix and aggregates into a markdown table. 5 models × 3 tasks minimum. Groq
gives us several models on one API; add one OpenRouter model so the "provider reliability" section
has an actual comparison and to prove the provider abstraction isn't Groq-shaped.

**Store our runs under `benchmarks/`, not `evaluations/`.** The `evaluations/EVAL_TYPE/<timestamp>/`
tree is reserved by the subject (VI.5) for official evaluation runs, and the moulinette already
writes there; dumping our own matrix into it mixes our artefacts with that reserved structure. Keep
them separate.

**Start the first matrix pass the moment `SWE-5` is green, not at M5.** 5 models × 3 SWE tasks at a
900 s ceiling, plus multi-GB image pulls and reruns on failure, is a half-day of wall-clock on one
machine and depends on daily free-tier quotas. Run it early (rerun later if needed) so we hit the
quota wall now, not in the last week.

---

**`BENCH-2` · Intermediary metrics extraction** · Dev 3 · M · BENCH-1

At least 2 of: step at which the agent first touches the file that appears in the final patch;
step at which failures first decrease; iterations between "tests pass" and `final_answer`. Parsing
`steps[]` from the JSON gives all three almost for free — manual inspection is allowed but a
30-line script is less painful.

---

**`BENCH-3` · Ablation** · all · M · BENCH-1

At least one before/after, same model, same tasks. Best candidates, in order of expected effect
size: (a) vague vs. methodology-rich system prompt, (b) with vs. without observation truncation,
(c) with vs. without `find_references`.

---

**`BENCH-4` · Write `BENCHMARK_REPORT.md`** · all · M · BENCH-1..3

The 6 required sections. Section 6 (conclusions) must actually name the model we ship and the
models we discard, justified by our own numbers.

**Decide now which `solution.json` files get committed.** The subject both requires the backing
`solution.json` to be present *and* forbids committing generated outputs — resolve it by committing
**only** the files that back the report (the 5 models × 3 tasks = 15 files) and nothing else. Write
the decision here so the answer is ready at the defense.

---

### Epic DOC — documentation & hardening

---

**`DOC-1` · `README.md`** · all · M · —

English. Italic first line with the three logins. Description / Instructions / Resources (incl. how
AI was used, per section) + the required extras: system architecture, agent loop, sandbox design,
tool implementation details, benchmark results.

Include two short paragraphs that pre-empt predictable misreadings: **(1)** why the MCP client lives
in the parent process and not in the network-less sandbox worker (the two are separate security
domains — see §3); **(2)** that our only outbound HTTP (`httpx`) is the LLM inference call, so a
generic HTTP-client grep isn't mistaken for fetching solutions.

---

**`DOC-2` · Sandbox exam dry run** · Dev 1 · M · SBX-3..7

Write our own `exam_sandbox.sh` equivalent covering import block, builtin block, network block, path
restriction, timeout, memory limit, and the MCP protocol (stdio + HTTP, tool discovery, manual
generation, structured feedback). This one has to pass **ALL** — no partial credit.

**Drive the CLI as a black box, not our internal classes.** The real exam feeds code on stdin to
`uv run sandbox [--mcp-stdio … | --mcp-server …] config.json`, so every capability — config load,
MCP connection, execution, error text — must be reachable and legible from that surface. Our dry-run
must exercise exactly that entry point; if something only works by importing our modules, it doesn't
count.

---

**`DOC-3` · Defense rehearsal** · all · S · everything

The evaluator will ask each of us to make a **live 2–5 minute modification** and re-run, then check
`solution.json` reflects it — the point is to prove our metrics are wired to real execution, not
fabricated. Rehearse the four field-provenance edits until anyone on the team can do each in under
5 minutes:

- inject a marker at the **start of the system prompt** → it must appear in `solution.json`'s
  `system_prompt`;
- print a marker **before user code runs** in the sandbox → it must appear in every step's
  `sandbox_output`;
- prepend a marker to the **code string sent to the sandbox** → it must appear in every step's
  `sandbox_input`;
- override the **logged `model_name`** without changing the real API call → the field changes and
  the task still solves (proves per-step metadata is independent of the call).

Also practice the ordinary knobs (change `max_iterations`, add a tool, change a truncation limit)
and rehearse the "why is your MCP client in the parent process" question. If someone can't find the
file for one of these, they don't understand their own codebase — fix that before the defense.

---

## 7. Suggested first sprint (week 1)

| Day | Dev 1 (Sandbox) | Dev 2 (Agent) | Dev 3 (Tools) |
|---|---|---|---|
| 1 | SETUP-1, SETUP-2 (together) | SETUP-2, SETUP-3 | SETUP-2, SETUP-4 |
| 2 | SBX-1 | CORE-1 | MCP-1 |
| 3 | SBX-1, SBX-2 | CORE-2, CORE-3 | MCP-3 |
| 4 | SBX-3 | CORE-4 | MCP-2 (with Dev 1) |
| 5 | SBX-4 | MBPP-1, MBPP-2 | SWE-1 |

End-of-week target: **M1** — one MBPP task solved end to end with limits disabled.

---

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Groq free-tier daily token cap exhausted mid-exam | Total failure | Multi-key rotation (`CORE-2`) + a second provider configured and tested, not just theoretically supported |
| MBPP 6k input budget | Fails 4/5 bar | Separate minimal prompt, 2–3 iteration target, budget guard forcing early submission |
| Sandbox escape found by evaluator | Instant fail on `exam_sandbox.sh` | Defence in depth: separate process + import guard + builtins + audit hook + rlimit; red-team each other's work |
| Docker containers left running | Explicitly graded | Context manager + atexit + startup sweep, verified in `SWE-1` tests |
| Overfitting to the 3 suggested SWE tasks | Exam draws at random | `SWE-7` generalisation run on 8–10 unseen tasks |
| Nobody understands the other two streams | Live-modification exercise fails | `DOC-3`, plus rotate reviewers on every PR |

---

## 9. Open decisions to make together

1. **Where does the sandbox run for SWE-bench** — inside the container, or on the host with MCP
   bridging in? Host + bridge is easier to debug and lets one sandbox implementation serve both
   benchmarks; in-container is closer to the drawn architecture. Recommendation: host + bridge.
2. **Second provider** — OpenRouter (widest model choice, needed anyway for 5 models) vs. Cerebras
   or Together. Free tiers move around; check current quotas before committing.
3. **Docker access** — `docker` CLI via `subprocess` (zero deps, trivially debuggable) vs. the
   `docker` SDK (cleaner API, one more dependency).
4. **Multi-agent?** Allowed but not required. Suggestion: don't, until M5 is green.
