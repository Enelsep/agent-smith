"""The five situations the subject says the model must never have to guess at.

Each is checked twice: that the notice carries the shared shape, and that it
says the specific thing the subject asks for -- an explanation of the repair, a
warning that output is partial, and so on. The wording is free to change; what
these pin is that the model is told, and told what to do next.
"""

from __future__ import annotations

import pytest

from agent_smith.sandbox import feedback

ALL_NOTICES = [
    feedback.no_code_block(),
    feedback.repaired_code("dropped prose before the code"),
    feedback.partial_output(),
    feedback.partial_output(printed=False),
    feedback.output_truncated(),
    feedback.edit_broke_file("a.py", "unexpected EOF"),
    feedback.edit_broke_file("a.py", "F401 unused import", syntax=False),
]


@pytest.mark.parametrize("text", ALL_NOTICES)
def test_every_notice_wears_the_sandbox_prefix(text: str) -> None:
    # Without it there is no telling a word from the sandbox apart from a word
    # the model's own code printed.
    assert text.startswith(feedback.PREFIX)


@pytest.mark.parametrize("text", ALL_NOTICES)
def test_every_notice_says_more_than_what_went_wrong(text: str) -> None:
    # The shape is "<what happened>. <what to do next>." -- two sentences, so
    # the model is never left holding a complaint with no way out of it.
    body = text.removeprefix(feedback.PREFIX).strip()
    assert len([s for s in body.split(". ") if s.strip()]) >= 2, body


def test_notice_does_not_double_the_full_stop() -> None:
    assert feedback.notice("something happened.", "Do this.") == (
        f"{feedback.PREFIX} something happened. Do this."
    )


# 1. No valid code block ------------------------------------------------------


def test_no_code_block_lists_the_formats_that_would_have_worked() -> None:
    text = feedback.no_code_block()
    for form in ("```python", "<invoke>", "<tool_call>", "Action:"):
        assert form in text


def test_no_code_block_keeps_the_extractors_own_reason() -> None:
    # The extractor knows the difference between prose and a Hermes block whose
    # JSON would not decode, and that difference is what the model must act on.
    text = feedback.no_code_block("Your hermes block was malformed: bad JSON")
    assert "hermes" in text
    assert "bad JSON" in text


def test_no_code_block_stops_listing_formats_once_one_was_used() -> None:
    # The model that produced a real block already knows how to frame one;
    # repeating the list spends input budget to tell it what it just did.
    used_one = feedback.no_code_block(
        "Your fenced block is not valid Python", saw_format=True
    )

    assert "<tool_call>" not in used_one
    assert "well-formed code block" in used_one


# 2. Malformed but interpreted anyway -----------------------------------------


def test_repaired_code_explains_how_it_was_repaired() -> None:
    # The subject asks for the repair to be explained, not just announced.
    text = feedback.repaired_code("closed an unterminated string or bracket")
    assert "closed an unterminated string or bracket" in text


def test_repaired_code_warns_the_result_is_not_from_what_was_sent() -> None:
    text = feedback.repaired_code("dropped prose after the code")
    assert "repaired" in text.lower()
    assert "not from what you sent" in text


def test_repaired_code_can_show_what_actually_ran() -> None:
    text = feedback.repaired_code("dropped prose before the code", "print(1)")
    assert "What actually ran:" in text
    assert "print(1)" in text


# 3. Timeout with partial output ----------------------------------------------


def test_partial_output_says_the_output_is_incomplete() -> None:
    text = feedback.partial_output()
    assert "time limit" in text
    assert "incomplete" in text


def test_partial_output_gives_different_advice_when_nothing_printed() -> None:
    # Partial output can be read and used; no output cannot, so the advice is
    # to print as you go rather than to treat what is there as partial.
    quiet = feedback.partial_output(printed=False)
    assert "before anything was printed" in quiet
    assert quiet != feedback.partial_output()


# 4. Output truncated by the size cap -----------------------------------------


def test_output_truncated_asks_for_a_narrower_slice_not_a_retry() -> None:
    text = feedback.output_truncated()
    assert "narrower" in text
    assert "rather than running this again" in text


def test_output_truncated_names_the_cap_when_it_is_known() -> None:
    assert "8000 characters" in feedback.output_truncated(8000)


# 5. An edit that broke the file ----------------------------------------------


def test_edit_broke_file_distinguishes_syntax_from_lint() -> None:
    broken = feedback.edit_broke_file("m.py", "unexpected EOF")
    linted = feedback.edit_broke_file("m.py", "F401 unused import", syntax=False)

    assert "syntax error" in broken
    assert "lint violation" in linted


def test_edit_broke_file_says_the_edit_was_applied() -> None:
    # Otherwise the model cannot tell whether to redo the edit or undo it.
    text = feedback.edit_broke_file("m.py", "unexpected EOF")
    assert "applied" in text
    assert "m.py" in text
