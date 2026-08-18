"""The interactive prompt: what it reads, when it runs it, how it leaves.

The loop is driven through `input_fn`/`write` rather than a terminal, so the
reading rules are testable without a pty. The cases that matter for the subject
-- a persistent namespace, restrictions still enforced, `final_answer` present,
a clean exit on `exit` and on EOF -- are run against a real `Sandbox` at the
bottom of the file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agent_smith.cli.sandbox import repl
from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import ExecResult, Outcome

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class FakeSandbox:
    """Records what it was asked to run and answers with what it was given."""

    def __init__(self, results: Iterable[ExecResult] = ()) -> None:
        self.executed: list[str] = []
        self.restarts = 0
        self._results = list(results)

    def execute(self, code: str) -> ExecResult:
        self.executed.append(code)
        if self._results:
            return self._results.pop(0)
        return ExecResult(outcome=Outcome.OK)

    def restart(self) -> None:
        self.restarts += 1


def feed(lines: Iterable[str]) -> Callable[[str], str]:
    """An `input_fn` that replays `lines` and then reports end of input."""
    remaining = list(lines)

    def read(prompt: str) -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read


def drive(lines: Iterable[str], sandbox: FakeSandbox | None = None) -> FakeSandbox:
    """Run the prompt over `lines` and hand back the sandbox it drove."""
    box = FakeSandbox() if sandbox is None else sandbox
    repl.run_repl(box, input_fn=feed(lines), write=lambda _: None)
    return box


# --------------------------------------------------------------------------
# Deciding when an entry is finished
# --------------------------------------------------------------------------


def test_a_single_statement_runs_as_soon_as_it_is_typed() -> None:
    assert drive(["x = 1"]).executed == ["x = 1"]


def test_consecutive_statements_each_run_on_their_own() -> None:
    # Pasting a sequence of statements has to behave: each line is complete on
    # arrival, so each runs, exactly as at CPython's prompt.
    box = drive(["import math", "print(math.pi)"])

    assert box.executed[0] == "import math"
    assert "math.pi" in box.executed[1]


def test_an_unfinished_block_waits_for_more_lines() -> None:
    assert drive(["def f():"]).executed == []


def test_a_block_waits_for_a_blank_line_even_once_it_parses() -> None:
    # `def f():` + `return 1` already parses. Running it there would make a
    # two-line body impossible to type, so the blank line is what submits.
    box = drive(["def f():", "    return 1", "    return 2", ""])

    assert len(box.executed) == 1
    assert "return 1" in box.executed[0]
    assert "return 2" in box.executed[0]


def test_an_unclosed_bracket_keeps_reading() -> None:
    box = drive(["x = (1,", "2)", ""])

    assert len(box.executed) == 1
    assert box.executed[0].startswith("x = (1,\n2)")


def test_a_syntax_error_is_reported_and_never_reaches_the_sandbox() -> None:
    written: list[str] = []
    box = FakeSandbox()

    repl.run_repl(box, input_fn=feed(["x ==="]), write=written.append)

    assert box.executed == []
    assert any("SyntaxError" in line for line in written)


def test_the_buffer_is_dropped_after_a_syntax_error() -> None:
    # Otherwise the bad line would poison every entry that followed it.
    box = drive(["x ===", "y = 2"])

    assert box.executed == ["y = 2"]


def test_blank_lines_at_an_empty_prompt_are_ignored() -> None:
    assert drive(["", "   ", "x = 1"]).executed == ["x = 1"]


# --------------------------------------------------------------------------
# Echoing values, the way a prompt is expected to
# --------------------------------------------------------------------------


def test_a_bare_expression_is_rewritten_to_print_its_value() -> None:
    rewritten = repl.echo_expression("1 + 1")

    assert "1 + 1" in rewritten
    assert "print(repr(_))" in rewritten


def test_a_statement_is_left_exactly_as_it_was_typed() -> None:
    assert repl.echo_expression("x = 1 + 1") == "x = 1 + 1"


def test_only_the_trailing_expression_of_an_entry_is_echoed() -> None:
    rewritten = repl.echo_expression("x = 2\nx * 3")

    assert rewritten.startswith("x = 2\n")
    assert rewritten.count("print(repr(_))") == 1


def test_an_expression_carrying_a_comment_still_compiles() -> None:
    # The closing bracket of the rewrite must not end up inside the comment.
    compile(repl.echo_expression("1 + 1  # add them"), "<test>", "exec")


def test_a_multi_line_expression_is_copied_verbatim() -> None:
    rewritten = repl.echo_expression("[\n    1,\n    2,\n]")

    compile(rewritten, "<test>", "exec")
    assert "    1,\n    2," in rewritten


def test_an_expression_sharing_a_line_with_a_statement_is_left_alone() -> None:
    # The rewrite splits on lines, so a semicolon would have it cut through
    # the middle of one. Not echoing is the safe answer.
    assert repl.echo_expression("x = 1; x") == "x = 1; x"


def test_source_that_does_not_parse_is_returned_untouched() -> None:
    assert repl.echo_expression("x ===") == "x ==="


# --------------------------------------------------------------------------
# Leaving
# --------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["exit", "exit()", "quit", "quit()", "  exit  "])
def test_the_exit_words_end_the_session(word: str) -> None:
    # `exit` is not among the sandbox builtins, so typing it can only work if
    # the prompt itself understands it.
    box = drive([word, "x = 1"])

    assert box.executed == []


def test_end_of_input_ends_the_session() -> None:
    # Ctrl-D. `feed` raises EOFError once its lines run out.
    drive(["x = 1"])  # returns rather than hanging or raising


def test_end_of_input_runs_a_block_still_waiting_for_its_blank_line() -> None:
    # Piped input rarely ends with a blank line, so EOF has to close the block.
    # Previously the buffer was dropped and the entry ran nowhere at all.
    box = drive(["for i in range(2):", "    print(i)"])

    assert len(box.executed) == 1
    assert "for i in range(2):" in box.executed[0]


def test_a_block_closed_by_a_blank_line_does_not_also_run_at_eof() -> None:
    box = drive(["for i in range(2):", "    print(i)", ""])

    assert len(box.executed) == 1


def test_end_of_input_inside_an_unfinished_block_reports_it() -> None:
    # The block can never run, but silence is what the old path gave for
    # everything; the user should hear that the entry was not executed.
    written: list[str] = []
    box = FakeSandbox()
    repl.run_repl(box, input_fn=feed(["for i in range(2):"]), write=written.append)

    assert box.executed == []
    assert any("SyntaxError" in line for line in written)


def test_exit_inside_an_unfinished_block_is_code_not_a_command() -> None:
    # At the continuation prompt the word is part of what is being typed.
    box = drive(["if True:", "    exit", ""])

    assert len(box.executed) == 1
    assert "exit" in box.executed[0]


def test_a_keyboard_interrupt_at_the_prompt_drops_the_entry_and_stays() -> None:
    def read(prompt: str) -> str:
        if not lines:
            raise EOFError
        line = lines.pop(0)
        if line is None:
            raise KeyboardInterrupt
        return line

    lines: list[str | None] = ["def f():", None, "x = 1"]
    box = FakeSandbox()
    written: list[str] = []

    repl.run_repl(box, input_fn=read, write=written.append)

    assert box.executed == ["x = 1"]
    assert "KeyboardInterrupt" in written


def test_a_keyboard_interrupt_during_execution_replaces_the_worker() -> None:
    # The worker is left holding a reply nobody will read, so it is replaced
    # rather than reused, and the session carries on.
    class Interrupting(FakeSandbox):
        def execute(self, code: str) -> ExecResult:
            raise KeyboardInterrupt

    box = Interrupting()
    written: list[str] = []

    repl.run_repl(box, input_fn=feed(["x = 1"]), write=written.append)

    assert box.restarts == 1
    assert any("interrupted" in line for line in written)


# --------------------------------------------------------------------------
# Against a real sandbox
# --------------------------------------------------------------------------


def test_the_namespace_persists_across_entries() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(
            sandbox,
            input_fn=feed(["x = 41", "print(x + 1)"]),
            write=written.append,
        )

    assert "42" in "\n".join(written)


def test_a_bare_expression_shows_its_value_at_the_prompt() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(sandbox, input_fn=feed(["40 + 2"]), write=written.append)

    assert "42" in "\n".join(written)


def test_a_statement_that_prints_nothing_shows_nothing() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(sandbox, input_fn=feed(["x = 1"]), write=written.append)

    assert [line for line in written if line] == []


def test_print_does_not_also_report_the_none_it_returns() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(sandbox, input_fn=feed(["print('hi')"]), write=written.append)

    assert "None" not in "\n".join(written)


def test_the_last_value_stays_reachable_as_underscore() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(
            sandbox,
            input_fn=feed(["6 * 7", "print(_ + 1)"]),
            write=written.append,
        )

    assert "43" in "\n".join(written)


def test_an_exception_is_shown_and_the_session_continues() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(
            sandbox,
            input_fn=feed(["1 / 0", "print('still here')"]),
            write=written.append,
        )

    printed = "\n".join(written)
    assert "ZeroDivisionError" in printed
    assert "still here" in printed


def test_the_import_guard_applies_to_typed_code() -> None:
    # The prompt is the sandbox, not a shell around it: a refused import is
    # refused here exactly as it is for the model.
    written: list[str] = []

    with Sandbox(timeout=5.0, authorized_imports=["math"]) as sandbox:
        repl.run_repl(sandbox, input_fn=feed(["import socket"]), write=written.append)

    assert "blocked" in "\n".join(written)


def test_the_timeout_applies_to_typed_code() -> None:
    written: list[str] = []

    with Sandbox(timeout=1.0) as sandbox:
        repl.run_repl(
            sandbox,
            input_fn=feed(["while True:", "    pass", ""]),
            write=written.append,
        )

    assert "soft_timeout" in "\n".join(written)


def test_final_answer_is_available_and_shown() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(
            sandbox,
            input_fn=feed(["final_answer('done')", "print('after')"]),
            write=written.append,
        )

    printed = "\n".join(written)
    assert "done" in printed
    # An answer ends a task, not a session: the prompt is still usable.
    assert "after" in printed


def test_a_function_defined_over_several_lines_is_usable_afterwards() -> None:
    written: list[str] = []

    with Sandbox(timeout=5.0) as sandbox:
        repl.run_repl(
            sandbox,
            input_fn=feed(
                ["def double(n):", "    m = n * 2", "    return m", "", "double(21)"]
            ),
            write=written.append,
        )

    assert "42" in "\n".join(written)


def test_the_banner_is_printed_before_the_first_prompt() -> None:
    written: list[str] = []

    repl.run_repl(
        FakeSandbox(), banner="hello", input_fn=feed([]), write=written.append
    )

    assert written[0] == "hello"


def test_a_blank_line_inside_a_block_does_not_chop_a_piped_script() -> None:
    # The prompt ends a block on a blank line; a file has them inside `if` and
    # `for` bodies as formatting. Read that way, the tail of the body arrives
    # as top-level statements and every one of them is an IndentationError.
    sandbox = FakeSandbox()
    script = (
        "found = []\n"
        "if True:\n"
        "    found.append('first')\n"
        "\n"
        "    found.append('second')\n"
        "print(len(found))\n"
    )

    repl.run_script(sandbox, script, write=lambda _: None)

    assert len(sandbox.executed) == 1
    assert "second" in sandbox.executed[0]


def test_an_empty_script_runs_nothing() -> None:
    sandbox = FakeSandbox()

    repl.run_script(sandbox, "   \n\n", write=lambda _: None)

    assert sandbox.executed == []
