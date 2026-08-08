"""
MCP Sandbox Integration module.

Provides the IPC-based bridge between the sandboxed worker (which executes code)
and the orchestrator (which holds the live, asynchronous network connections).

This design ensures the sandbox remains entirely isolated: no network sockets,
event loops, or unpicklable clients cross the process boundary.
"""

import logging
from collections.abc import Callable
from typing import Any

from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.mcp.registry import MCPToolRegistry
from agent_smith.mcp.wrapper import build_inspect_signature

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. SANDBOX SIDE (SYNC STUBS)
# ==============================================================================


def create_tool_stub(
    tool_def: MCPToolDefinition,
    ipc_request_fn: Callable[[str, dict[str, Any]], str],
) -> Callable[..., str]:
    """
    Creates a synchronous stub for an MCP tool to be injected into the sandbox.

    When called by the sandboxed code, this stub forwards the execution request
    (tool name + kwargs) to the parent process via the provided IPC callback.

    Args:
        tool_def: Metadata of the tool (must be picklable/JSON serializable).
        ipc_request_fn: A function handling IPC (e.g., sending over mp.Pipe)
                        that blocks until the parent returns the string result.

    Returns:
        A synchronous function mimicking the real tool's name and docstring.
    """

    signature, _ = build_inspect_signature(tool_def.input_schema)

    def stub(*args: Any, **kwargs: Any) -> str:
        # An MCP call is a JSON object, so the wire format is keyword-only. A
        # model writing Python writes `read_file("path")` anyway, and the
        # published schema says which parameter that is. The aliases the
        # signature carries are deliberately dropped: the orchestrator-side
        # wrapper does the sanitised-to-original renaming, and doing it twice
        # here would send names that wrapper cannot bind.
        if args:
            kwargs = dict(signature.bind(*args, **kwargs).arguments)
        logger.debug(
            f"Sandbox stub called for tool '{tool_def.name}' with args: {kwargs}"
        )
        return ipc_request_fn(tool_def.name, kwargs)

    stub.__name__ = tool_def.name
    stub.__doc__ = tool_def.description
    return stub


def get_sandbox_tool_stubs(
    tool_defs: list[MCPToolDefinition],
    ipc_request_fn: Callable[[str, dict[str, Any]], str],
) -> dict[str, Callable[..., str]]:
    """
    Builds a dictionary of synchronous tool stubs for the sandbox namespace.

    Args:
        tool_defs: List of available tool definitions from the orchestrator.
        ipc_request_fn: The worker's IPC pipe communication function.

    Returns:
        A mapping of {tool_name: stub_callable} ready for namespace injection.
    """
    stubs: dict[str, Callable[..., str]] = {}
    for tool_def in tool_defs:
        stubs[tool_def.name] = create_tool_stub(tool_def, ipc_request_fn)
    return stubs


# ==============================================================================
# 2. ORCHESTRATOR / PARENT SIDE (ASYNC EXECUTION)
# ==============================================================================


async def execute_mcp_tool_call(
    registry: MCPToolRegistry,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """
    Parent-side handler: Executes the actual async MCP tool using the live registry.
    This must be run on the main process's event loop, where the actual
    MCP client (HTTP / Stdio subprocess) is connected and alive.

    Args:
        registry: The loaded MCP Tool Registry.
        tool_name: The requested tool name from the IPC pipe.
        arguments: The keyword arguments requested by the sandbox agent.

    Returns:
        The text output of the tool, or a gracefully formatted error message.
    """
    if tool_name not in registry.tools:
        return f"Error: Tool '{tool_name}' not found in registry."

    tool_wrapper = registry.tools[tool_name]
    try:
        # Execute the actual network/async tool wrapper
        return await tool_wrapper(**arguments)
    except TypeError as exc:
        logger.error(f"Type error calling tool '{tool_name}': {exc}")
        return f"Error: Invalid arguments for '{tool_name}'. Details: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Execution error in tool '{tool_name}': {exc}")
        return f"Error: Tool execution failed: {exc}"
