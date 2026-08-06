"""
LLM Tool Schema Formatter for MCP tools.

Provides conversion utility functions to format MCPToolDefinition objects
into parameter schemas required by various LLM providers (OpenAI, Anthropic)
or plain-text ReAct system prompts.
"""

import logging
from typing import Any

from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.mcp.wrapper import sanitize_param_name

logger = logging.getLogger(__name__)


def to_openai_tool(tool_def: MCPToolDefinition) -> dict[str, Any]:
    """
    Converts an MCPToolDefinition into OpenAI Function/Tool calling schema.

    Args:
        tool_def: The MCP tool definition object.

    Returns:
        dict formatted according to OpenAI API tool specification.
    """
    # Normalisation : OpenAI rejette les paramètres vides {}.
    # On force un schéma d'objet vide valide si aucun paramètre n'est fourni.
    parameters = tool_def.input_schema
    if not parameters:
        parameters = {"type": "object", "properties": {}}

    return {
        "type": "function",
        "function": {
            "name": tool_def.name,
            "description": tool_def.description,
            "parameters": parameters,
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


MANUAL_HEADING = "## Tools"

NO_TOOLS_MANUAL = (
    f"{MANUAL_HEADING}\n\nNo MCP server is connected: no tools are available."
)

MANUAL_PREAMBLE = (
    "Available in the sandbox namespace as ordinary Python functions. Each "
    "takes keyword arguments and returns a string. They run outside the "
    "sandbox, so its import and file restrictions do not apply to their work."
)

SUMMARY_LIMIT = 110
"""How much of a tool's description survives into the manual. Servers write
paragraphs; the prompt pays for every word of them on every single turn."""

_JSON_TYPES = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def _annotation(spec: dict[str, Any]) -> str:
    """The Python type for one property, or "" when the schema does not say.

    An unknown or absent type is left unannotated rather than guessed at:
    `x: Any` costs tokens to say nothing.
    """
    declared = spec.get("type")
    if isinstance(declared, list):
        # ["string", "null"] is how a nullable field is usually written.
        declared = next((entry for entry in declared if entry != "null"), None)
    python = _JSON_TYPES.get(declared) if isinstance(declared, str) else None
    return f": {python}" if python else ""


def _summary(text: str) -> str:
    """One line, capped. Cut on a word boundary so it does not end mid-token."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= SUMMARY_LIMIT:
        return collapsed
    clipped = collapsed[:SUMMARY_LIMIT].rsplit(" ", 1)[0]
    return f"{clipped.rstrip('.,;:')}..."


def _call_signature(input_schema: dict[str, Any]) -> str:
    """The call the model must write, from the schema the server published.

    Two things here are correctness rather than presentation. Parameter names
    are sanitised, because that is what the generated wrapper accepts -- a
    server offering `start-line` is called as `start_line=`, and a manual
    quoting the raw name would teach a call that raises. Required parameters
    come first, matching `build_inspect_signature`, so the order shown is the
    order the real function has.
    """
    properties = input_schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return ""
    required = set(input_schema.get("required") or [])

    ordered = [name for name in properties if name in required]
    ordered += [name for name in properties if name not in required]

    rendered: list[str] = []
    for name in ordered:
        spec = properties[name] if isinstance(properties[name], dict) else {}
        annotation = _annotation(spec)
        param = f"{sanitize_param_name(name)}{annotation}"
        if name not in required:
            param += " = None" if annotation else "=None"
        rendered.append(param)
    return ", ".join(rendered)


def to_sandbox_manual(tools: list[MCPToolDefinition]) -> str:
    """The tool section of the sandbox manual, as compact markdown.

    Built from `list_tools()` alone, so connecting a different server -- or an
    unknown one -- documents whatever that server offers with no code change.
    Regenerate it on every connection; it describes one session's tools, not
    a fixed set.

    One line per tool, because this is re-sent to the model on every turn and
    the input budget is cumulative over the task.
    """
    if not tools:
        return NO_TOOLS_MANUAL

    lines = [MANUAL_HEADING, "", MANUAL_PREAMBLE, ""]
    for tool in tools:
        call = f"{tool.name}({_call_signature(tool.input_schema)})"
        description = _summary(tool.description or "")
        lines.append(f"- `{call}`" + (f" - {description}" if description else ""))
    return "\n".join(lines)


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
