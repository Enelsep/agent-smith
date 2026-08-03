"""
MCP Tool Registry module.

Manages tool discovery and aggregation across multiple MCP clients.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition
from agent_smith.mcp.wrapper import build_mcp_tool_wrapper

logger = logging.getLogger(__name__)


class MCPToolRegistry:
    """
    Aggregates tools discovered across registered MCP clients.
    """

    def __init__(self, *clients: MCPClientProtocol) -> None:
        self._clients: list[MCPClientProtocol] = list(clients)
        self._wrappers: dict[str, Callable[..., Awaitable[str]]] = {}
        self._tool_definitions: dict[str, MCPToolDefinition] = {}

    @property
    def tools(self) -> dict[str, Callable[..., Awaitable[str]]]:
        """Returns a shallow copy of registered tool wrappers."""
        return dict(self._wrappers)

    def register_client(self, client: MCPClientProtocol) -> None:
        """Registers a new client to the registry."""
        if client not in self._clients:
            self._clients.append(client)

    async def discover_tools(self) -> dict[str, Callable[..., Awaitable[str]]]:
        """
        Discovers tools from all registered clients.
        Isolates failures per-tool so a single malformed tool schema does not
        drop neighboring tools.
        """
        self._wrappers.clear()
        self._tool_definitions.clear()

        for client in self._clients:
            try:
                tool_defs = await client.list_tools()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to list tools from client {client}: {exc}")
                continue

            for tool_def in tool_defs:
                try:
                    wrapper = build_mcp_tool_wrapper(client, tool_def)
                    if tool_def.name in self._wrappers:
                        logger.warning(
                            f"Tool name collision: '{tool_def.name}' already registered. Overwriting."
                        )
                    self._wrappers[tool_def.name] = wrapper
                    self._tool_definitions[tool_def.name] = tool_def
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"Skipping tool '{tool_def.name}' due to wrapper build error: {exc}"
                    )

        return self.tools

    def get_tool(self, tool_name: str) -> Callable[..., Awaitable[str]] | None:
        """Retrieves the wrapper function of a tool by its name."""
        return self._wrappers.get(tool_name)

    def get_tool_definitions(self) -> list[MCPToolDefinition]:
        """Returns the list of raw MCP definitions (MCPToolDefinition)."""
        return list(self._tool_definitions.values())

    async def call_tool_by_name(self, tool_name: str, *args: Any, **kwargs: Any) -> str:
        """
        Executes a tool by its name.
        """
        wrapper = self.get_tool(tool_name)
        if not wrapper:
            return (
                f"Observation: Tool '{tool_name}' is not available in the MCP registry."
            )
        return await wrapper(*args, **kwargs)
