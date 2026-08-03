"""
MCP Sandbox Integration module.

Converts asynchronous MCP tool wrappers into synchronous callables and
injects them into the sandbox worker's execution namespace.
"""

import asyncio
import concurrent.futures
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_smith.mcp.registry import MCPToolRegistry

logger = logging.getLogger(__name__)


def make_sync_tool(async_tool: Callable[..., Awaitable[str]]) -> Callable[..., str]:
    """
    Wraps an async MCP tool wrapper into a synchronous function so it can be called
    directly by code executing inside the synchronous sandbox environment.

    Args:
        async_tool: The asynchronous tool wrapper callable.

    Returns:
        A synchronous function with preserved name, docstring, and signature.
    """

    def sync_wrapper(*args: Any, **kwargs: Any) -> str:
        async def _run() -> str:
            return await async_tool(*args, **kwargs)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # If an event loop is already running, run the coroutine in a separate thread
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future: concurrent.futures.Future[str] = executor.submit(
                    asyncio.run, _run()
                )
                return future.result()
        else:
            return asyncio.run(_run())

    # Preserve metadata for introspection inside the sandbox
    sync_wrapper.__name__ = getattr(async_tool, "__name__", "mcp_tool")
    sync_wrapper.__doc__ = getattr(async_tool, "__doc__", "")
    if hasattr(async_tool, "__signature__"):
        sync_wrapper.__signature__ = async_tool.__signature__  # type: ignore[attr-defined]
    return sync_wrapper


def get_sync_tools(
    registry: MCPToolRegistry,
) -> dict[str, Callable[..., str]]:
    """
    Retrieves all discovered tools from the MCPToolRegistry and converts
    them into synchronous, sandbox-compatible callables.

    Args:
        registry: The MCP Tool Registry containing discovered tools.

    Returns:
        A dictionary mapping tool names to synchronous wrapper functions.
    """
    sync_tools: dict[str, Callable[..., str]] = {}
    for name, async_wrapper in registry.tools.items():
        sync_tools[name] = make_sync_tool(async_wrapper)
    return sync_tools


def inject_tools_into_namespace(
    namespace: dict[str, Any], registry: MCPToolRegistry
) -> None:
    """
    Injects all synchronous MCP tool functions directly into a sandbox namespace dictionary.

    Args:
        namespace: The global execution namespace dictionary used by exec().
        registry: The MCP Tool Registry.
    """
    sync_tools = get_sync_tools(registry)
    for tool_name, sync_fn in sync_tools.items():
        namespace[tool_name] = sync_fn
        logger.debug(f"Injected MCP tool '{tool_name}' into sandbox namespace.")
