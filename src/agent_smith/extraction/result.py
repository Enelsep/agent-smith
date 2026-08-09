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
    """The outcome of one extraction, successful or not.

    Frozen for the reason `LLMResponse` is frozen: this is a record of what
    happened, not state anyone should edit afterwards.

    `code` and `failure` are exclusive — exactly one is set. `strategy` is
    deliberately outside that pair. A marker that matched and then failed still
    names itself, so a failure can read "the Hermes block was there and its JSON
    would not decode" instead of "nothing worked". `strategy` is `None` only when
    no marker matched at all, which is itself the useful distinction between a
    model that formatted badly and a model that answered in prose.
    """

    model_config = ConfigDict(frozen=True)

    code: str | None = None
    strategy: Strategy | None = None
    repaired: bool = False
    repair_note: str | None = None
    failure: str | None = None
    only_comments: bool = False
    """The block parsed and held nothing but comments.

    A failure of its own kind: the format was right and the reasoning is all
    there, so telling this model to send a well-formed block would send it round
    the same loop. What it needs to hear is that comments do not run.
    """

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> "ExtractionResult":
        if (self.code is None) == (self.failure is None):
            raise ValueError("exactly one of code / failure must be set")
        return self
