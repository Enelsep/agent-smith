from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class MCPToolDefinition(BaseModel, frozen=True):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class MCPClientProtocol(Protocol):
    """Unified interface for MCP clients (supporting both
    Stdio and Streamable HTTP)."""

    async def connect(self) -> None:
        """Establish or re-establish connection with the MCP server."""
        ...

    async def disconnect(self) -> None:
        """Gracefully close the connection and clean up resources."""
        ...

    async def list_tools(self) -> list[MCPToolDefinition]:
        """List available tools provided by the MCP server."""
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """
        Execute a tool.
        In case of failure or a dead server, it must surface as a
        readable observation string
        rather than raising an unhandled exception that crashes
        the orchestrator.
        """
        ...
