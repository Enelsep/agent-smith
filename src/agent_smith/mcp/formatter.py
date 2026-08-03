"""
LLM Tool Schema Formatter for MCP tools.

Provides conversion utility functions to format MCPToolDefinition objects
into parameter schemas required by various LLM providers (OpenAI, Anthropic)
or plain-text ReAct system prompts.
"""

import logging
from typing import Any

from agent_smith.mcp.protocol import MCPToolDefinition

logger = logging.getLogger(__name__)


def to_openai_tool(tool_def: MCPToolDefinition) -> dict[str, Any]:
    """
    Converts an MCPToolDefinition into OpenAI Function/Tool calling schema.

    Args:
        tool_def: The MCP tool definition object.

    Returns:
        dict formatted according to OpenAI API tool specification.
    """
    return {
        "type": "function",
        "function": {
            "name": tool_def.name,
            "description": tool_def.description,
            "parameters": tool_def.input_schema,
        },
    }


def to_openai_tools(tools: list[MCPToolDefinition]) -> list[dict[str, Any]]:
    """
    Converts a list of MCPToolDefinition objects into OpenAI tools format.

    Args:
        tools: list of MCP tool definitions.

    Returns:
        list of dicts formatted for OpenAI API `tools` parameter.
    """
    return [to_openai_tool(tool) for tool in tools]


def to_anthropic_tool(tool_def: MCPToolDefinition) -> dict[str, Any]:
    """
    Converts an MCPToolDefinition into Anthropic Tool calling schema.

    Args:
        tool_def: The MCP tool definition object.

    Returns:
        dict formatted according to Anthropic API tool specification.
    """
    return {
        "name": tool_def.name,
        "description": tool_def.description,
        "input_schema": tool_def.input_schema,
    }


def to_anthropic_tools(tools: list[MCPToolDefinition]) -> list[dict[str, Any]]:
    """
    Converts a list of MCPToolDefinition objects into Anthropic tools format.

    Args:
        tools: list of MCP tool definitions.

    Returns:
        list of dicts formatted for Anthropic API `tools` parameter.
    """
    return [to_anthropic_tool(tool) for tool in tools]


def to_react_prompt_string(tools: list[MCPToolDefinition]) -> str:
    """
    Formats a list of MCPToolDefinition objects into a human-readable text string
    suitable for injection into plain-text ReAct system prompts.

    Args:
        tools: list of MCP tool definitions.

    Returns:
        Formatted string listing available tools and their parameters.
    """
    if not tools:
        return "No tools available."

    lines: list[str] = ["Available Tools:"]
    for tool in tools:
        lines.append(f"\n- Tool: {tool.name}")
        if tool.description:
            lines.append(f"  Description: {tool.description}")

        properties = tool.input_schema.get("properties", {})
        required = set(tool.input_schema.get("required", []))

        if properties:
            lines.append("  Parameters:")
            for prop_name, prop_spec in properties.items():
                p_type = prop_spec.get("type", "any")
                is_req = "required" if prop_name in required else "optional"
                p_desc = prop_spec.get("description", "")
                desc_str = f" - {p_desc}" if p_desc else ""
                lines.append(f"    * {prop_name} ({p_type}, {is_req}){desc_str}")

    return "\n".join(lines)
