"""
Unit tests for MCP-3 Sandbox Integration.
"""

import inspect
import unittest
from typing import Any
from unittest.mock import AsyncMock

from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition
from agent_smith.mcp.registry import MCPToolRegistry
from agent_smith.mcp.sandbox_integration import (
    get_sync_tools,
    inject_tools_into_namespace,
    make_sync_tool,
)


class DummyMCPClient(MCPClientProtocol):
    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def list_tools(self) -> list[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="read_file",
                description="Read file content",
                input_schema={
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "Target path"}
                    },
                    "required": ["filepath"],
                },
            )
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        return f"Content of {arguments.get('filepath')}"


class TestMCPSandboxIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_make_sync_tool_execution(self) -> None:
        async_mock = AsyncMock(return_value="mocked output")
        async_mock.__name__ = "dummy_tool"
        async_mock.__doc__ = "Dummy docstring"

        sync_tool = make_sync_tool(async_mock)

        self.assertEqual(sync_tool.__name__, "dummy_tool")
        self.assertEqual(sync_tool.__doc__, "Dummy docstring")

        # Execute synchronously
        result = sync_tool()
        self.assertEqual(result, "mocked output")

    async def test_get_sync_tools_and_namespace_injection(self) -> None:
        client = DummyMCPClient()
        registry = MCPToolRegistry(client)
        await registry.discover_tools()

        sync_tools = get_sync_tools(registry)
        self.assertIn("read_file", sync_tools)

        # Introspect signature
        sig = inspect.signature(sync_tools["read_file"])
        self.assertIn("filepath", sig.parameters)

        # Call synchronous tool
        res = sync_tools["read_file"](filepath="test.txt")
        self.assertEqual(res, "Content of test.txt")

        # Inject into execution namespace and execute via exec()
        namespace: dict[str, Any] = {}
        inject_tools_into_namespace(namespace, registry)

        self.assertIn("read_file", namespace)

        # Simulate exec() as performed in worker.py
        code = "output = read_file(filepath='hello.py')"
        exec(code, namespace)  # noqa: S102
        self.assertEqual(namespace["output"], "Content of hello.py")


if __name__ == "__main__":
    unittest.main()
