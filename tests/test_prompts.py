"""The prompt files, and the loader that reads them out of the package."""

from pathlib import Path

import pytest

from agent_smith.agent.budget import CHARS_PER_TOKEN
from agent_smith.cli.mbpp.prompt import build_system_prompt
from agent_smith.config.loader import load_sandbox_config
from agent_smith.prompts import load_prompt

REPO_ROOT = Path(__file__).resolve().parents[1]

MBPP_TOKEN_CEILING = 800
"""What CORE-6 allows the MBPP system prompt, in estimate_tokens' own units."""


def test_a_prompt_is_read_from_the_package() -> None:
    assert "final_answer" in load_prompt("mbpp")


def test_an_unknown_prompt_says_which_one_was_missing() -> None:
    with pytest.raises(FileNotFoundError, match="nosuch"):
        load_prompt("nosuch")


def test_the_mbpp_prompt_fits_the_budget_core6_allows() -> None:
    # Measured on the prompt that is actually sent, not the file it is read
    # from: `{imports}` stands for the real allowlist, which is twenty modules
    # today and is edited independently of the prompt. Measured the way the
    # budget guard measures, so this number and the one the run is charged for
    # cannot drift apart.
    sandbox = load_sandbox_config(REPO_ROOT / "sandbox_template.json")
    estimated = len(build_system_prompt(sandbox.authorized_imports)) // CHARS_PER_TOKEN

    assert estimated <= MBPP_TOKEN_CEILING, f"{estimated} tokens"


def test_the_mbpp_prompt_keeps_the_delimiter_the_stack_agrees_on() -> None:
    # `<end_code>` is a stop sequence in models.json and a fence closer in
    # extraction.strategies. A prompt that stops naming it lets the model run
    # past its block and invent the observation it should have waited for.
    assert "<end_code>" in load_prompt("mbpp")


def test_the_mbpp_prompt_still_has_its_imports_placeholder() -> None:
    assert "{imports}" in load_prompt("mbpp")


def test_the_mbpp_prompt_no_longer_asks_for_a_thought_preamble() -> None:
    # The turn is the code block. A `Thought:` line invites the prose that
    # measurement showed the model runs out of tokens producing.
    assert "Thought:" not in load_prompt("mbpp")


def test_the_mbpp_prompt_forbids_fitting_the_visible_assertions() -> None:
    text = load_prompt("mbpp").lower()

    assert "hidden" in text
    assert "subset" in text


def test_the_mbpp_prompt_requires_the_solution_to_carry_its_imports() -> None:
    # A submitted function that uses `math` without importing it passes the
    # visible run, where the import already happened, and fails the hidden one.
    text = load_prompt("mbpp").lower()

    assert "import" in text
    assert "final_answer" in text


SWEBENCH_TOOLS = (
    "read_file",
    "edit_file",
    "list_files",
    "search_code",
    "search_function_or_class_definition_in_code",
    "find_references",
    "run_tests",
    "get_patch",
    "run_command",
)


def test_the_swebench_prompt_names_every_tool_the_server_exposes() -> None:
    text = load_prompt("swebench")

    missing = [tool for tool in SWEBENCH_TOOLS if tool not in text]
    assert not missing, f"tools absent from the prompt: {missing}"


def test_the_swebench_prompt_uses_the_same_turn_shape_as_mbpp() -> None:
    text = load_prompt("swebench")

    assert "<end_code>" in text
    assert "Thought:" not in text


def test_the_swebench_prompt_says_it_is_unvalidated() -> None:
    # No agent_swebench CLI exists to run it against, so the file says so
    # rather than reading as advice anyone has tested.
    assert "unvalidated" in load_prompt("swebench").lower()


def test_the_swebench_prompt_names_the_only_call_that_ends_a_task() -> None:
    # The loop ends on `Outcome.FINAL_ANSWER`, which only `final_answer()`
    # raises. A prompt that presents `get_patch()` as the way to submit teaches
    # a protocol that cannot terminate: the model gets its diff back as an
    # observation and keeps going until the iteration budget runs out. The
    # subject spells the submission out — "SWE-bench: call
    # final_answer(get_patch())".
    text = load_prompt("swebench")

    assert "final_answer(get_patch())" in text
