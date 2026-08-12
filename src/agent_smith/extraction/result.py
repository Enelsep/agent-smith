"""What one extraction attempt produced."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class Strategy(str, Enum):
    """The shape the model used to ask for something.

    `str, Enum` rather than `StrEnum`: `requires-python` is `>=3.10` and
    `StrEnum` arrived in 3.11.
    """

    FENCED = "fenced"
    XML = "xml"
    HERMES = "hermes"
    REACT = "react"
    BARE = "bare"


class ExtractionResult(BaseModel):
    """The outcome of one extraction, successful or not."""

    model_config = ConfigDict(frozen=True)

    code: str | None = None
    strategy: Strategy | None = None
    repaired: bool = False
    repair_note: str | None = None
    failure: str | None = None
    only_comments: bool = False

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> "ExtractionResult":
        if (self.code is None) == (self.failure is None):
            raise ValueError("exactly one of code / failure must be set")
        return self
