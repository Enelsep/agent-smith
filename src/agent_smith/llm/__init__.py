"""Talking to an OpenAI-compatible inference endpoint.

This package re-exports the contract and nothing else, so importing it does not
load an HTTP client. The provider that speaks HTTP is reached by naming it:
`from agent_smith.llm.openai_compat import OpenAICompatProvider`.
"""

from agent_smith.llm.errors import ProviderError
from agent_smith.llm.protocol import KeySource, LLMProvider, Message
from agent_smith.llm.response import LLMResponse

__all__ = [
    "KeySource",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "ProviderError",
]
