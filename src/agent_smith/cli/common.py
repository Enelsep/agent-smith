"""What both benchmark CLIs do identically.

Neither `agent_mbpp` nor `agent_swebench` is the owner of writing a solution
file or building a provider, and having each keep its own copy means fixing
anything here twice.
"""

from __future__ import annotations

from pathlib import Path

from agent_smith.config import ResolvedConfig
from agent_smith.llm.keypool import KeyPool
from agent_smith.llm.retry import RetryingProvider
from agent_smith.models.contract import SolutionOutput

RETRY_SHARE_OF_WALL_CLOCK = 6.0
"""How much of a task's clock one completion may spend being retried.

`retry.py` picks its default this way from MBPP's 120 s, and says why: several
calls share the clock, so no single one may hold much of it."""


def failed_run(benchmark: str, task_id: str, error: str) -> SolutionOutput:
    """A valid solution for a run that never got as far as an iteration."""
    return SolutionOutput(
        task_id=task_id,
        benchmark=benchmark,
        success=False,
        solution="",
        iterations=0,
        total_requests=0,
        total_input_tokens=0,
        total_output_tokens=0,
        total_time_seconds=0.0,
        error=error,
    )


def write_solution(path: Path, solution: SolutionOutput) -> None:
    """Write the solution to the requested path, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(solution.model_dump_json(indent=2), encoding="utf-8")


def build_provider(
    config: ResolvedConfig,
    *,
    wall_clock_seconds: float | None = None,
) -> RetryingProvider:
    """The provider stack, from the resolved configuration.

    `openai_compat` is imported here rather than at module scope: it is the
    only module that pulls in an HTTP client, and
    `tests/test_llm_import_boundary.py` checks that the rest of the tree does
    not.

    `wall_clock_seconds` is the benchmark's whole-task ceiling, and a sixth of
    it becomes the retry budget for one completion -- the ratio `retry.py`
    already fixes for MBPP, applied to whichever benchmark is running. A
    benchmark with a wider clock can then sleep off a rate limit its provider
    announced: 20 s cannot wait out the 60 s Mistral asks for, and the run dies
    on the first 429 rather than pausing for it.
    """
    from agent_smith.llm.openai_compat import OpenAICompatProvider

    pool = KeyPool(config.api_keys)
    inner = OpenAICompatProvider(
        config.base_url,
        config.model_name,
        pool,
        stop=config.stop,
        max_tokens=config.max_tokens,
    )
    if wall_clock_seconds is None:
        return RetryingProvider(inner, pool)
    return RetryingProvider(
        inner, pool, max_elapsed_seconds=wall_clock_seconds / RETRY_SHARE_OF_WALL_CLOCK
    )
