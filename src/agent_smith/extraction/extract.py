"""Walking the chain and turning its outcome into a result."""

import ast

from agent_smith.extraction.repair import repair_python
from agent_smith.extraction.result import ExtractionResult, Strategy
from agent_smith.extraction.strategies import STRATEGY_CHAIN, Candidate, PayloadError

_NOTHING_MATCHED = (
    "No code found in your reply. Send a ```python fenced block, an <invoke> block, "
    "a <tool_call> block, or an Action: / Action Input: pair."
)


def extract_code(text: str) -> ExtractionResult:
    """Extract runnable Python from a model reply. Never raises.

    The first strategy whose marker matches owns the outcome; the chain is not
    re-entered on failure. The markers are distinctive enough that a reply
    carrying two is rare, and falling through would turn one clear failure into
    a pile of diagnostics none of which is the real one.

    `Exception` is caught at the boundary because CORE-4 carries a hard "must
    never raise" obligation — a crash scores as an automatic fail. `BaseException`
    is not caught: `KeyboardInterrupt` and `SystemExit` must keep propagating.
    """
    try:
        return _walk(text)
    except Exception as unexpected:  # noqa: BLE001 - the boundary is the point
        return ExtractionResult(failure=f"Could not read your reply: {unexpected}")


def _walk(text: str) -> ExtractionResult:
    for strategy, produce in STRATEGY_CHAIN:
        try:
            candidate = produce(text)
        except PayloadError as broken:
            return ExtractionResult(
                strategy=strategy,
                failure=f"Your {strategy.value} block was malformed: {broken.reason}.",
            )
        if candidate is not None:
            return _finish(strategy, candidate)
    return ExtractionResult(failure=_NOTHING_MATCHED)


def _finish(strategy: Strategy, candidate: Candidate) -> ExtractionResult:
    if _parses(candidate.code):
        return ExtractionResult(
            code=candidate.code,
            strategy=strategy,
            repaired=candidate.repair_note is not None,
            repair_note=candidate.repair_note,
        )
    if candidate.repair_note is not None:
        # The one repair was already spent while matching.
        return ExtractionResult(
            strategy=strategy,
            failure=(
                f"Your {strategy.value} block still does not parse after I "
                f"{candidate.repair_note}."
            ),
        )
    repaired = repair_python(candidate.code)
    if repaired is None:
        return ExtractionResult(
            strategy=strategy,
            failure=f"Your {strategy.value} block is not valid Python.",
        )
    code, note = repaired
    return ExtractionResult(
        code=code, strategy=strategy, repaired=True, repair_note=note
    )


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    return True
