import ast

from agent_smith.extraction.repair import repair_python
from agent_smith.extraction.result import ExtractionResult, Strategy
from agent_smith.extraction.strategies import STRATEGY_CHAIN, Candidate, PayloadError

_NOTHING_MATCHED = "No code block was found in your reply"


def extract_code(text: str, *, step: int) -> ExtractionResult:
    """Extract runnable Python from a model reply. Never raises."""
    try:
        return _walk(text, step)
    except Exception as unexpected:  # noqa: BLE001 - the boundary is the point
        return ExtractionResult(failure=f"Could not read your reply: {unexpected}")


def _walk(text: str, step: int) -> ExtractionResult:
    for strategy, produce in STRATEGY_CHAIN:
        try:
            candidate = produce(text, step)
        except PayloadError as broken:
            return ExtractionResult(
                strategy=strategy,
                failure=f"Your {strategy.value} block was malformed: {broken.reason}.",
            )
        if candidate is not None:
            return _finish(strategy, candidate)
    return ExtractionResult(failure=_NOTHING_MATCHED)


def _finish(strategy: Strategy, candidate: Candidate) -> ExtractionResult:
    code, note = candidate.code, candidate.repair_note
    module = _parse(code)
    if module is None:
        if note is not None:
            # The one repair was already spent while matching.
            return ExtractionResult(
                strategy=strategy,
                failure=(
                    f"Your {strategy.value} block still does not parse after I {note}."
                ),
            )
        repaired = repair_python(code)
        if repaired is None:
            return ExtractionResult(
                strategy=strategy,
                failure=f"Your {strategy.value} block is not valid Python.",
            )
        code, note = repaired
        module = _parse(code)
    if module is not None and not module.body:
        return ExtractionResult(
            strategy=strategy,
            failure=f"Your {strategy.value} block holds only comments.",
            only_comments=True,
        )
    return ExtractionResult(
        code=code, strategy=strategy, repaired=note is not None, repair_note=note
    )


def _parse(code: str) -> ast.Module | None:
    try:
        return ast.parse(code)
    except (SyntaxError, ValueError):
        return None
