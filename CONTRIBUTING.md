# Contributing — Agent Smith

Short internal guide for the team. The full task breakdown lives in
[`AGENT_SMITH_PROJECT_PLAN.md`](./AGENT_SMITH_PROJECT_PLAN.md); tasks are tracked in Linear.

## Streams & ownership

| Stream | Area | Owner | Cards |
|---|---|---|---|
| A | Sandbox | Eliott (`epesnel`) | `SBX-*`, `DOC-2` |
| B | Agent | Jerome (`jbarthel`) | `CORE-*`, `MBPP-*` |
| C | Tools | Quentin (`qbourine`) | `MCP-*`, `TOOL-*`, `SWE-*` |

Shared cards (`SETUP-2`, `SWE-5`, `BENCH-*`, `DOC-1`/`DOC-3`) go to whoever is free; pair up when a
card spans two streams.

## Workflow — trunk-based, one branch per issue

- **`main` is protected and always green.** No direct pushes.
- One **short-lived branch per Linear issue** → PR → **squash-merge** to `main`. Merge within a day
  or two — don't let branches age or drift.
- **Land the `SETUP` epic on `main` first**, especially `SETUP-2` (the frozen Pydantic contract):
  everything depends on it, and parallel feature branches are only safe once the contract is on
  `main`.
- Don't start a branch whose blocker (Linear **"blocked by"**) hasn't merged yet, or you'll rebase
  in circles.

## Branch names

- Copy the name from the Linear issue (**"Copy git branch name"**), e.g.
  `jbarthel/ags-14-orchestrator-loop`. This auto-links the PR to the issue and moves it
  *In Progress → Done*.
- The integration keys off **Linear's identifier** (`AGS-N`), **not** the plan card id (`CORE-4`) —
  the card id lives only in the issue title.

## Pull requests

- Keep them **small** (one card). Small PRs are what make cross-stream review realistic.
- The **reviewer must be from a different stream** — rotate. Everyone has to understand all three
  streams for the live-modification exercise at defense.
- Mergeable when: **1 approval**, **`make check` green** (`ruff check` + `ruff format --check` +
  `mypy` over the whole repository + `pytest`), and it **doesn't
  break `exam_sandbox.sh`**. There is no remote CI — `make check` is the gate, and each of us runs
  it before pushing.

## Formatting

- **`ruff format` is the formatter, and `make check` enforces it.** Run `uv run ruff format .`
  before pushing; if you let your editor format on save, point it at Ruff so it agrees with the
  gate. Nothing else — `black` is not a project dependency, and `flake8` lints at 79 columns where
  we are at 88.
- The tree was reformatted in one dedicated commit. Run this **once per clone** so `git blame`
  keeps crediting the real authors: `git config blame.ignoreRevsFile .git-blame-ignore-revs`.
  GitHub reads the file on its own.
- If you ever need another repo-wide reformat, give it its **own commit** and add its SHA to
  `.git-blame-ignore-revs` in the same PR.

## Documentation

- **Docs follow the same rule as code: branch → PR. No direct commit to `main`**, even for a
  markdown file.
- What belongs on `main`: **design specs** in `docs/superpowers/specs/`, shipped in the PR of the
  card they describe, and the plan/audit documents at the root. A spec states decisions and
  trade-offs — it is what we defend at the oral.
- What does **not** belong on `main`: throwaway working documents — session checklists, drafts,
  scratch notes. Keep them outside the repo; add the path to your own `.git/info/exclude` (local,
  never pushed) rather than to the shared `.gitignore`.

## Commits & hygiene

- Present tense, one logical change per commit; reference the card id in the body when useful.
- **Never commit secrets.** API keys load from `.env`, which is gitignored. **Nothing enforces
  this** — there is no hook, nothing scans our commits — so it is on us, and `git add -f` walks
  straight past `.gitignore`. At evaluation the keys come from the `--envfile` handed to the exam
  scripts; the ones we can leak are our own free-tier development keys, and a burned quota costs us
  a run.
- **Respect `.gitignore`:** the moulinette checkout, `.env` and generated run outputs stay out of
  git. Only the `solution.json` files that back `BENCHMARK_REPORT.md` are committed.
- **Personal files stay out of the shared `.gitignore`.** Anything that concerns only your machine
  goes in `.git/info/exclude`.
