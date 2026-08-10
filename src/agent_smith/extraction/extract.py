"""Walking the chain and turning its outcome into a result."""

import ast

from agent_smith.extraction.repair import repair_python
from agent_smith.extraction.result import ExtractionResult, Strategy
from agent_smith.extraction.strategies import STRATEGY_CHAIN, Candidate, PayloadError

# The diagnosis only. What to send instead is `sandbox.feedback.no_code_block`'s
# to say, so the instruction is worded in one place and cannot be given twice.
_NOTHING_MATCHED = "No code block was found in your reply"


def extract_code(text: str, *, step: int) -> ExtractionResult:
    """Extract runnable Python from a model reply. Never raises.

    The first strategy whose marker matches owns the outcome; the chain is not
    re-entered on failure. The markers are distinctive enough that a reply
    carrying two is rare, and falling through would turn one clear failure into
    a pile of diagnostics none of which is the real one.

    `step` is the 1-indexed iteration number, and it is required rather than
    defaulted. It names the variables a normalised tool call assigns to, and the
    sandbox namespace outlives the step — so a caller that forgot to pass it
    would silently overwrite the previous step's values. A default would make
    that the easy path.

    `Exception` is caught at the boundary because CORE-4 carries a hard "must
    never raise" obligation — a crash scores as an automatic fail. `BaseException`
    is not caught: `KeyboardInterrupt` and `SystemExit` must keep propagating.
    """
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
        # A block of nothing but comments parses, runs, and prints nothing, so
        # the sandbox has no complaint to make about it. Naming it here is the
        # only place the difference between "it printed nothing" and "there was
        # nothing to print" is still visible.
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
