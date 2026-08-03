"""What one completion is worth measuring."""

from pydantic import BaseModel, ConfigDict


class LLMResponse(BaseModel):
    """One completion and its measurements.

    These are the fields of `StepMetrics` that only the provider can know;
    CORE-4 completes the record with `step`, `sandbox_input` and
    `sandbox_output`. Frozen, because a measurement is not mutable state.

    `api_url` is the full endpoint that was called, not the base URL: it is
    per-step evidence of what was contacted.

    `retries` is `0` here and stays `0` until CORE-2. That is the correct
    value, not a placeholder — the contract defines `0` as "the first attempt
    succeeded".
    """

    model_config = ConfigDict(frozen=True)

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str
    api_url: str
    retries: int = 0
