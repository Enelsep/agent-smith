from collections.abc import Sequence
from typing import Literal, Protocol, TypedDict

from agent_smith.llm.response import LLMResponse


class Message(TypedDict):
    """One chat message. A TypedDict rather than a bare dict so mypy can check
    the call site and `role` is a `Literal` to prevent typos.

    These three roles are what a ReAct loop needs. Native tool-calling would
    require a `tool` role and other fields, at which point this whole TypedDict
    would need to be revisited.
    """

    role: Literal["system", "user", "assistant"]
    content: str


class KeySource(Protocol):
    """Supplies the API key for the next request."""

    def api_key(self) -> str: ...


class LLMProvider(Protocol):
    """Asks a model for a completion."""

    def complete(
        self,
        messages: Sequence[Message],
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...
