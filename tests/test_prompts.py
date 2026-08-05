"""The prompt files, and the loader that reads them out of the package."""

import pytest

from agent_smith.agent.budget import CHARS_PER_TOKEN
from agent_smith.prompts import load_prompt

MBPP_TOKEN_CEILING = 800
"""What CORE-6 allows the MBPP system prompt, in estimate_tokens' own units."""


def test_a_prompt_is_read_from_the_package() -> None:
    assert "final_answer" in load_prompt("mbpp")


def test_an_unknown_prompt_says_which_one_was_missing() -> None:
    with pytest.raises(FileNotFoundError, match="nosuch"):
        load_prompt("nosuch")


def test_the_mbpp_prompt_fits_the_budget_core6_allows() -> None:
    # Measured the way the budget guard measures, so the number here and the
    # number the run is charged for cannot drift apart.
    estimated = len(load_prompt("mbpp")) // CHARS_PER_TOKEN

    assert estimated <= MBPP_TOKEN_CEILING, f"{estimated} tokens"


def test_the_mbpp_prompt_keeps_the_delimiter_the_stack_agrees_on() -> None:
    # `<end_code>` is a stop sequence in models.json and a fence closer in
    # extraction.strategies. A prompt that stops naming it lets the model run
    # past its block and invent the observation it should have waited for.
    assert "<end_code>" in load_prompt("mbpp")


def test_the_mbpp_prompt_still_has_its_imports_placeholder() -> None:
    assert "{imports}" in load_prompt("mbpp")
