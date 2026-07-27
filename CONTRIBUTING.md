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
- Mergeable when: **1 approval**, **CI green** (`ruff` + `pytest`), and it **doesn't break
  `exam_sandbox.sh`**.

## Commits & hygiene

- Present tense, one logical change per commit; reference the card id in the body when useful.
- **Never commit secrets.** API keys load from `.env` (gitignored); the pre-commit hook blocks
  `gsk_` / `sk-` patterns.
- **Respect `.gitignore`:** the moulinette checkout, the subject PDF, `.env`, and generated run
  outputs stay out of git. Only the `solution.json` files that back `BENCHMARK_REPORT.md` are
  committed.
