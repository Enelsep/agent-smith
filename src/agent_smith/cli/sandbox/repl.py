from __future__ import annotations

import ast
import codeop
from typing import TYPE_CHECKING, Protocol

from agent_smith.cli.sandbox.render import MARKER, render

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_smith.sandbox.protocol import ExecResult

PROMPT = ">>> "
CONTINUATION = "... "

FILENAME = "<sandbox>"

EXIT_WORDS = frozenset({"exit", "exit()", "quit", "quit()"})

ECHO_NAME = "_"
"""Where a bare expression's value is parked before being shown, so that the
last result stays reachable exactly as it does at any Python prompt."""

INTERRUPTED = (
    f"{MARKER} interrupted. The worker was restarted, so every variable and "
    f"function defined earlier is gone."
)


class Sandbox(Protocol):
    """What the prompt needs from a sandbox.

    Structural, for the reason `agent.loop` gives: a fake satisfies it without
    inheriting a constructor that spawns a subprocess, and this module never
    names `agent_smith.sandbox.process`.
    """

    def execute(self, code: str) -> ExecResult: ...

    def restart(self) -> None: ...


def is_incomplete(source: str) -> bool:
    """Whether `source` is the start of an entry rather than all of it.

    Raises `SyntaxError` for source that can never complete, which is how a
    typo is reported without troubling the worker with it.
    """
    return codeop.compile_command(source, FILENAME, "exec") is None


def ends_entry(lines: list[str]) -> bool:
    """Whether a buffer that already parses should run now or keep growing.

    One line runs the moment it parses, which is what makes pasting a sequence
    of statements behave. Anything longer waits for a blank line, as CPython's
    own prompt does, because a block is valid long before it is finished:
    `def f():` followed by `return 1` already parses, and running it there
    would make a two-line function body impossible to type.
    """
    return len(lines) == 1 or not lines[-1].strip()


def echo_expression(source: str) -> str:
    """Rewrite a trailing bare expression so that its value is printed."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    if not tree.body:
        return source

    last = tree.body[-1]
    if not isinstance(last, ast.Expr) or last.col_offset != 0:
        return source

    lines = source.splitlines()
    head, expression = lines[: last.lineno - 1], lines[last.lineno - 1 :]
    while expression and not expression[-1].strip():
        expression.pop()

    body = "\n".join(expression)
    assignment = f"{ECHO_NAME} = ({body})"
    if len(expression) > 1 or not _parses(assignment):
        assignment = f"{ECHO_NAME} = (\n{body}\n)"

    return "\n".join(
        [
            *head,
            assignment,
            f"if {ECHO_NAME} is not None:\n    print(repr({ECHO_NAME}))",
        ]
    )


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def run_repl(
    sandbox: Sandbox,
    *,
    banner: str = "",
    input_fn: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Read entries and run them until `exit` or end of input.

    Returns rather than exits: the caller owns the sandbox and has a worker to
    shut down, which a `SystemExit` raised from in here would skip.
    """
    if banner:
        write(banner)

    lines: list[str] = []
    while True:
        eof = False
        try:
            line = input_fn(CONTINUATION if lines else PROMPT)
        except EOFError:
            write("")
            if not lines:
                return
            eof, line = True, ""
        except KeyboardInterrupt:
            write("KeyboardInterrupt")
            lines = []
            continue

        if not lines and line.strip() in EXIT_WORDS:
            return

        lines.append(line)
        source = "\n".join(lines)
        if not source.strip():
            lines = []
            continue

        try:
            incomplete = is_incomplete(source)
        except (SyntaxError, ValueError, MemoryError, RecursionError) as malformed:
            write(f"{type(malformed).__name__}: {malformed}")
            lines = []
            continue

        if incomplete or not ends_entry(lines):
            if eof:
                write("SyntaxError: unexpected end of input")
                return
            continue

        entry, lines = source, []
        _run_entry(sandbox, entry, write)
        if eof:
            return


def _run_entry(sandbox: Sandbox, source: str, write: Callable[[str], None]) -> None:
    """Run one finished entry and print whatever it had to say.

    A Ctrl-C that lands while the worker holds the terminal reaches this
    process too. The worker is left mid-entry with a reply nobody will read, so
    it is replaced rather than reused.
    """
    try:
        result = sandbox.execute(echo_expression(source))
    except KeyboardInterrupt:
        sandbox.restart()
        write(INTERRUPTED)
        return

    rendered = render(result)
    if rendered:
        write(rendered)
