"""
MCP Tool Wrapper module.

Dynamically constructs Python functions with proper signatures and docstrings
from MCPToolDefinition objects.
"""

import inspect
import keyword
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition

logger = logging.getLogger(__name__)


def sanitize_param_name(name: str) -> str:
    """
    Sanitizes a parameter name to ensure it is a valid Python identifier.
    Converts kebab-case to snake_case and appends '_' to Python keywords.
    """
    sanitized = re.sub(r"\W", "_", name)
    if re.match(r"^\d", sanitized):
        sanitized = f"_{sanitized}"
    if keyword.iskeyword(sanitized):
        sanitized = f"{sanitized}_"
    return sanitized


def build_inspect_signature(
    input_schema: dict[str, Any],
) -> tuple[inspect.Signature, dict[str, str]]:
    """
    Builds an inspect.Signature from a JSON Schema input_schema and creates
    a mapping from sanitized parameter names back to original schema names.

    Returns:
        tuple containing (Signature, alias_map where key=sanitized, val=original)
    """
    properties = input_schema.get("properties", {})
    required_params = set(input_schema.get("required", []))

    parameters: list[inspect.Parameter] = []
    alias_map: dict[str, str] = {}

    req_items = []
    opt_items = []

    for name, spec in properties.items():
        if name in required_params:
            req_items.append((name, spec))
        else:
            opt_items.append((name, spec))

    for orig_name, spec in req_items + opt_items:
        clean_name = sanitize_param_name(orig_name)
        alias_map[clean_name] = orig_name

        is_req = orig_name in required_params
        default = inspect.Parameter.empty if is_req else None

        parameters.append(
            inspect.Parameter(
                name=clean_name,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
            )
        )

    return inspect.Signature(parameters), alias_map


def build_mcp_tool_wrapper(
    client: MCPClientProtocol,
    tool_def: MCPToolDefinition,
) -> Callable[..., Awaitable[str]]:
    """
    Constructs an async Python wrapper for an MCP tool.
    """
    sig, alias_map = build_inspect_signature(tool_def.input_schema)

    async def wrapper(*args: Any, **kwargs: Any) -> str:
        bound = sig.bind(*args, **kwargs)

        mcp_arguments: dict[str, Any] = {}
        for clean_name, val in bound.arguments.items():
            if val is not None:
                orig_name = alias_map.get(clean_name, clean_name)
                mcp_arguments[orig_name] = val

        return await client.call_tool(tool_def.name, mcp_arguments)

    wrapper.__name__ = sanitize_param_name(tool_def.name)
    wrapper.__doc__ = tool_def.description or f"MCP tool '{tool_def.name}'"
    wrapper.__signature__ = sig  # type: ignore[attr-defined]

    return wrapper
