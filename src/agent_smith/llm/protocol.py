"""The interfaces the rest of the system codes against.

`KeySource` is the seam CORE-2 attaches to. The provider consults it on every
request rather than once at construction, which is what lets a pool hand out a
different key from one call to the next.
"""

from collections.abc import Sequence
from typing import Literal, Protocol, TypedDict

from agent_smith.llm.response import LLMResponse


class Message(TypedDict):
    """One chat message. A TypedDict rather than a bare dict so mypy can check
    the call site.

    `role` is a `Literal` rather than a `str` because a misspelt role is not
    caught by any endpoint: it is forwarded, and the model answers something
    plausible to a conversation we did not mean to send. These three are what a
    ReAct loop needs, observations being fed back as user turns. Native
    tool-calling would need a `tool` role, but it would also need `tool_calls`
    and `tool_call_id` here — so the day this Literal is too narrow, the
    TypedDict is too narrow too, and that is the right moment to reopen both.
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
