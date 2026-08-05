"""What the MBPP agent tells the model, and what it tells it about the task.

Kept apart from the wiring in `main` because CORE-6 iterates on this text
without touching the CLI, and because `SolutionOutput.system_prompt` records it
verbatim for provenance.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_smith.models.contract import MBPPTaskInput

# `<end_code>` is a configured stop sequence in `models.json` and a fence closer
# in `agent_smith.extraction.strategies`. The prompt has to name the same token,
# or the model runs past its code block and invents the observation it should
# have waited for.
_TEMPLATE = """\
You are an autonomous coding agent. You solve one Python task by writing code
that is really executed, reading the real output, and iterating.

Each turn you produce exactly one Thought and one Code block:

Thought: one or two sentences on what you will try and why.
```python
# python that runs in a persistent namespace
```<end_code>

Then STOP. Do not write an Observation - the real execution result is given to
you in the next message. Anything you invent instead will be wrong.

The namespace persists between turns: variables, functions and imports you
define stay available. Only these modules may be imported: {imports}.

final_answer(source_code_string) ends the task. Call it with the source code of
your function as a string, not with the result of calling it.

Method:
1. Write the function, then call it on the given examples and print the results.
2. Read the printed output. If it disagrees with an example, fix the function.
3. The examples shown are a visible subset - hidden tests also run. Solve the
   problem the description states, not just the cases you can see.
4. When the printed results match, call final_answer with the function source.

Example of a good turn:

Thought: I will implement the function and check it against the two examples.
```python
def add(a, b):
    return a + b

print(add(2, 3), add(-1, 1))
```<end_code>

Example of the final turn, once the printed results matched:

Thought: The outputs match the examples, so I submit the function source.
```python
final_answer('''def add(a, b):
    return a + b
''')
```<end_code>

Submitting is also a code block: final_answer is a function that exists in the
namespace, and writing the solution without calling it does not end the task.
"""


def build_system_prompt(authorized_imports: Sequence[str]) -> str:
    """The system prompt, quoting the allowlist the sandbox will actually enforce.

    The list is passed in rather than hardcoded so it cannot drift from
    `sandbox_template.json`: a model told it may import something the sandbox
    refuses spends an iteration finding out.
    """
    return _TEMPLATE.format(imports=", ".join(authorized_imports))


def task_prompt(task: MBPPTaskInput) -> str:
    """The one task, in the order a solver needs it: what, then how, then proof.

    `test_list` carries the public assertions only, and a solution is judged on
    the full set. The visible ones are therefore labelled as a subset rather
    than presented as the specification, so the model solves the description
    instead of fitting the examples.
    """
    parts = [
        task.task_definition,
        "",
        "Your function must use exactly this signature:",
        task.function_definition,
    ]
    if task.test_imports:
        parts += ["", "The tests run with these imports:", *task.test_imports]
    if task.test_list:
        parts += [
            "",
            "Visible tests - a subset, hidden tests also run:",
            *task.test_list,
        ]
    return "\n".join(parts)
