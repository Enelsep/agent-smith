"""The interactive sandbox: a prompt that runs what you type in the worker.

`uv run sandbox` with no task is this. Entries cross the same process boundary
the agent's code does and land in the same persistent namespace, so the import
guard, the filesystem policy, the timeout and the memory cap all apply exactly
as they do to a model's code. That is the whole reason to offer a prompt: it is
the only way to try a restriction by hand and watch it refuse.

Nothing here executes anything. Source is compiled once, in this process, to
decide whether the user has finished typing -- the code object is thrown away
and the text is handed to the worker, which is where every guard lives.
"""

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
    """Rewrite a trailing bare expression so that its value is printed.

    `exec` evaluates expression statements and discards the result, so `1 + 1`
    would otherwise run and show nothing at all. Binding to `_` first keeps the
    value reachable; printing only when it is not None is what stops `print(x)`
    from reporting the None that `print` returns.

    The expression is copied verbatim from the source rather than unparsed from
    the tree, so what the worker runs is what was typed. Anything that cannot
    be rewritten safely -- a statement tucked after a semicolon, source that no
    longer parses -- comes back untouched and simply does not echo.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    if not tree.body:
        return source

    last = tree.body[-1]
    # A non-zero column means the expression shares its line with something
    # else (`x = 1; x`), and the line-based split below would cut through it.
    if not isinstance(last, ast.Expr) or last.col_offset != 0:
        return source

    lines = source.splitlines()
    head, expression = lines[: last.lineno - 1], lines[last.lineno - 1 :]
    while expression and not expression[-1].strip():
        expression.pop()

    body = "\n".join(expression)
    assignment = f"{ECHO_NAME} = ({body})"
    if len(expression) > 1 or not _parses(assignment):
        # Either the expression spans lines, or a trailing comment on it would
        # swallow a closing bracket left on the same line. Both are fixed by
        # giving the bracket a line of its own, at the cost of shifting the
        # line numbers a traceback would report by one.
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
            # End of input closes a pending block the way a blank line does.
            # Piped input rarely ends with one, and returning here instead
            # would discard the entry without running it or saying so.
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
                # Nothing safe to run: the block never finished. Say so rather
                # than drop it, which is what the old EOF path did to
                # everything.
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
