"""What one completion is worth measuring."""

from pydantic import BaseModel, ConfigDict


class LLMResponse(BaseModel):
    """One completion and its measurements.

    These are the fields of `StepMetrics` that only the provider can know;
    CORE-4 completes the record with `step`, `sandbox_input` and
    `sandbox_output`. Frozen, because a measurement is not mutable state.

    `api_url` is the full endpoint that was called, not the base URL: it is
    per-step evidence of what was contacted.

    `retries` is `0` on a response the provider builds, which the contract
    defines as "the first attempt succeeded". The retry layer records a higher
    count with `after_retries()`.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str
    api_url: str
    retries: int = 0

    def after_retries(self, count: int) -> "LLMResponse":
        """The same completion, recording how many attempts it took to get it.

        Constructing rather than `model_copy(update=...)`, which would skip
        validation. `retries` is excluded from the dump because passing it
        alongside the keyword argument is a duplicate-argument `TypeError`.
        """
        return LLMResponse(**self.model_dump(exclude={"retries"}), retries=count)
