"""Holding the transcript flat: what survives a compaction and what shrinks."""

import json
from itertools import pairwise
from pathlib import Path

from agent_smith.agent.budget import estimate_tokens
from agent_smith.agent.history import TRUNCATION_MARKER, compact_history
from agent_smith.llm import Message


def a_history(steps: int, *, reply_chars: int = 400) -> list[Message]:
    """A transcript with `steps` completed steps, each reply carrying code."""
    messages: list[Message] = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "TASK"},
    ]
    for n in range(1, steps + 1):
        prose = f"Thought: {'reasoning ' * reply_chars}"
        messages.append(
            {
                "role": "assistant",
                "content": f"{prose}\n```py\nprint({n})\n```<end_code>",
            }
        )
        messages.append({"role": "user", "content": f"observation {n}"})
    return messages


def test_the_system_and_task_prompts_are_never_touched() -> None:
    compacted = compact_history(a_history(5))

    assert compacted[0] == {"role": "system", "content": "SYSTEM"}
    assert compacted[1] == {"role": "user", "content": "TASK"}


def test_the_last_step_keeps_the_prose_the_model_just_wrote() -> None:
    compacted = compact_history(a_history(3), verbatim_steps=1)

    assert "reasoning" in compacted[-2]["content"]
    assert compacted[-1] == {"role": "user", "content": "observation 3"}


def test_an_older_step_keeps_its_code_and_loses_its_prose() -> None:
    compacted = compact_history(a_history(3), verbatim_steps=1)

    first_reply = compacted[2]["content"]
    assert "print(1)" in first_reply
    assert "reasoning" not in first_reply
    assert TRUNCATION_MARKER in first_reply


def test_an_older_step_keeps_its_observation_verbatim() -> None:
    # Observations measure 0-33 characters in practice, so they are kept
    # whole: shrinking them would buy nothing and lose the sandbox's answer.
    compacted = compact_history(a_history(3), verbatim_steps=1)

    assert compacted[3] == {"role": "user", "content": "observation 1"}


def test_a_reduced_reply_is_still_an_assistant_turn() -> None:
    compacted = compact_history(a_history(3), verbatim_steps=1)

    assert compacted[2]["role"] == "assistant"


def test_every_step_survives_as_a_pair() -> None:
    compacted = compact_history(a_history(4), verbatim_steps=1)

    assert len(compacted) == len(a_history(4))


def test_the_transcript_stops_growing_as_steps_accumulate() -> None:
    # The point of the whole card: per-call size must flatten, not climb.
    sizes = [
        sum(len(message["content"]) for message in compact_history(a_history(n)))
        for n in range(2, 8)
    ]
    growth = [later - earlier for earlier, later in pairwise(sizes)]

    assert max(growth) < 200, f"still growing: {sizes}"


def test_a_history_with_no_steps_is_returned_unchanged() -> None:
    bare: list[Message] = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "TASK"},
    ]

    assert compact_history(bare) == bare


def test_a_history_shorter_than_the_window_is_returned_unchanged() -> None:
    one_step = a_history(1)

    assert compact_history(one_step, verbatim_steps=1) == one_step


def test_zero_verbatim_steps_reduces_every_step() -> None:
    compacted = compact_history(a_history(2), verbatim_steps=0)

    assert "reasoning" not in compacted[2]["content"]
    assert "reasoning" not in compacted[4]["content"]


def test_a_reply_with_no_extractable_code_keeps_only_the_marker() -> None:
    messages: list[Message] = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "I am thinking about it and wrote nothing."},
        {"role": "user", "content": "observation 1"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "observation 2"},
    ]

    compacted = compact_history(messages, verbatim_steps=1)

    assert compacted[2]["content"] == TRUNCATION_MARKER


def test_an_odd_tail_does_not_raise() -> None:
    # The seam is called after an observation is appended, so this should not
    # occur. It is handled anyway: this function must never fail a run.
    messages = [*a_history(2), {"role": "assistant", "content": "dangling"}]

    compact_history(messages)  # type: ignore[arg-type]


def test_the_input_list_is_not_mutated() -> None:
    original = a_history(3)
    before = [dict(message) for message in original]

    compact_history(original)

    assert [dict(message) for message in original] == before


FIXTURE = Path(__file__).parent / "fixtures" / "mbpp_160_transcript.json"
MBPP_INPUT_CEILING = 6000
"""The cumulative input-token budget MBPP-3 has to fit inside."""


def _calls_that_fit(steps: list[dict[str, str]], system: str, verbatim: int) -> int:
    """How many calls the recorded run would afford under the ceiling."""
    history: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "TASK"},
    ]
    spent = 0
    for index, step in enumerate(steps):
        spent += estimate_tokens(compact_history(history, verbatim_steps=verbatim))
        if spent > MBPP_INPUT_CEILING:
            return index
        history.append({"role": "assistant", "content": step["llm_output"]})
        history.append({"role": "user", "content": step["sandbox_output"]})
    return len(steps)


def test_compaction_buys_another_iteration_on_the_worst_recorded_task() -> None:
    # MBPP task 160 ran seven iterations and cost 17 639 cumulative input
    # tokens against a 6 000 ceiling. Compaction does not make it pass -- it
    # needed seven and gets five -- but the headroom it buys is the deliverable,
    # so it is measured rather than assumed.
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    steps, system = recorded["steps"], recorded["system_prompt"]

    assert _calls_that_fit(steps, system, verbatim=99) == 4
    assert _calls_that_fit(steps, system, verbatim=1) == 5
