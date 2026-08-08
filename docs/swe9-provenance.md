# Where a SWE-bench patch is allowed to come from

The subject scores a run zero if the agent fetches a solution from outside the
task, or reuses a memorised patch without genuine exploration. This is what we
decided, and what each decision was measured against.

## The container has no route out once the agent has a say

The sandbox denies the worker a socket, and the import allowlist denies it
`urllib` and `socket`. Neither reaches inside the container: `run_command` runs
a shell there, and a shell has whatever network the container has.

Measured on `sweb.eval.x86_64.sympy_1776_sympy-14711`, a plain `docker run`
leaves a container that reaches `pypi.org` and returns 200. So one shell command
was all that stood between an agent and a published fix.

`DockerManager.cut_network` detaches the container from every network it is on,
immediately after the dependency bootstrap and before the agent's first turn.
The bootstrap goes first because installing `ruff`, `jedi` and `ripgrep` is the
one step that needs the route, and it runs before any model output exists.

Nothing legitimate is lost. The repository is already checked out and the test
suite runs offline — verified by running the task's own tests in a detached
container: 4 passed, 0 failed.

## `run_command` keeps its full scope, including git

The card asked whether to restrict `git log` and `git show` in `/testbed`. The
answer is no, on evidence rather than on principle.

The image's history is truncated at the parent of the fix. `HEAD` is an empty
commit authored by `SWE-bench <setup@swebench.config>`, sitting on top of the
project's ordinary history; `git log --all` reaches nothing beyond it,
`origin/master` does not resolve, and there are no remote refs. There is no
commit in that repository that contains the answer, so nothing to hide.

Restricting the tool would also cost more than it buys. `run_command` is one of
the nine the subject makes mandatory, and reading a repository's history is how
anyone locates the change that introduced a bug. Blocking it would trade a real
capability for protection against a leak that is not there.

## The prompt never asks for recall

`prompts/swebench.md` says to search, read, reproduce, edit and test. It names
no repository, no upstream project, no issue tracker, and asks nowhere for what
the model knows about a bug — only for what it can observe. The method section
is written from a task solved by hand through these same tools, and carries the
order of the gestures with none of that task's content.

## What we do not claim

Thirteen of our fifteen benchmark runs edited a file before reproducing
anything, and one model opened a run with the exact fix on turn 1 having read
nothing at all.

That is not smuggled knowledge. Two of the three tasks carry a `hints_text`
field that names the fix — one says the change is `using=db` in `.save()`, the
other says which line holds the typo and what it should read — and that field is
part of the task the evaluation hands us. `cli/swebench/prompt.py` passes it to
the model along with the problem statement, because dropping half the statement
would be answering a different question than the one asked.

The distinction we hold to: the agent may use everything in the task it is
given, and nothing from anywhere else. The first is the statement. The second is
what the network cut now makes impossible rather than merely forbidden.

The task with no hints, `sympy__sympy-14711`, is the one where the models
diverge and the one our exam run failed — which is the shape you would expect if
the hints, and not memorisation, are what drive the early edits.
