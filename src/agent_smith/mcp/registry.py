"""
MCP Tool Registry: Discovers tools from MCP clients and manages callable dynamic wrappers.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition
from agent_smith.mcp.wrapper import create_tool_wrapper

logger = logging.getLogger(__name__)


class MCPToolRegistry:
    """
    MCP tool registry.

    Roles:
    1. Aggregates one or multiple MCP clients (e.g., local Stdio client, remote Streamable HTTP client).
    2. Dynamically discovers their tools via `list_tools()`.
    3. Transforms each tool definition into a Pydantic-validated Python wrapper.
    4. Provides a dictionary/namespace of directly callable tools.
    """

    def __init__(self, client: MCPClientProtocol | None = None) -> None:
        self._clients: list[MCPClientProtocol] = []
        if client:
            self._clients.append(client)

        self._wrappers: dict[str, Callable[..., Awaitable[str]]] = {}
        self._tool_definitions: dict[str, MCPToolDefinition] = {}

    def add_client(self, client: MCPClientProtocol) -> None:
        """Adds an MCP client to the registry."""
        if client not in self._clients:
            self._clients.append(client)

    async def discover_tools(self) -> dict[str, Callable[..., Awaitable[str]]]:
        """
        Queries all registered MCP clients, generates dynamic wrappers,
        and returns the dictionary of callable functions.
        """
        self._wrappers.clear()
        self._tool_definitions.clear()

        for client in self._clients:
            try:
                tools = await client.list_tools()
                for tool_def in tools:
                    tool_name = tool_def.name
                    if tool_name in self._wrappers:
                        logger.warning(
                            f"Tool name conflict detected: '{tool_name}' already exists. "
                            f"It will be replaced by the latest registered client's definition."
                        )

                    self._tool_definitions[tool_name] = tool_def

                    # Capture current client reference for the RPC call
                    current_client = client

                    async def _call_fn(
                        name: str,
                        args: dict[str, Any],
                        c: MCPClientProtocol = current_client,
                    ) -> str:
                        return await c.call_tool(name, args)

                    wrapper = create_tool_wrapper(
                        tool_name=tool_def.name,
                        description=tool_def.description,
                        input_schema=tool_def.input_schema,
                        call_tool_fn=_call_fn,
                    )

                    self._wrappers[tool_name] = wrapper

            except Exception as e:  # noqa: BLE001
                logger.error(f"Error discovering tools on client: {e}")

        return self._wrappers

    def get_tool(self, tool_name: str) -> Callable[..., Awaitable[str]] | None:
        """Retrieves the wrapper function of a tool by its name."""
        return self._wrappers.get(tool_name)

    @property
    def tools(self) -> dict[str, Callable[..., Awaitable[str]]]:
        """Returns the full dictionary {tool_name: wrapper_function}."""
        return self._wrappers

    def get_tool_definitions(self) -> list[MCPToolDefinition]:
        """Returns the list of raw MCP definitions (MCPToolDefinition)."""
        return list(self._tool_definitions.values())

    async def call_tool_by_name(self, tool_name: str, *args: Any, **kwargs: Any) -> str:
        """
        Executes a tool by its name with built-in Pydantic validation.
        """
        wrapper = self.get_tool(tool_name)
        if not wrapper:
            return (
                f"Observation: Tool '{tool_name}' is not available in the MCP registry."
            )
        return await wrapper(*args, **kwargs)
