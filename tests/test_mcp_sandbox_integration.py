"""
Unit tests for MCP-3 Sandbox Integration (IPC Model).
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.mcp.registry import MCPToolRegistry
from agent_smith.mcp.sandbox_integration import (
    create_tool_stub,
    execute_mcp_tool_call,
    get_sandbox_tool_stubs,
)


class TestMCPSandboxIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tool_def = MCPToolDefinition(
            name="test_tool",
            description="A test tool for sandbox",
            input_schema={"type": "object", "properties": {}},
        )

    def test_create_tool_stub(self) -> None:
        # Mock the IPC request function that would normally send data over a pipe
        mock_ipc = MagicMock(return_value="ipc_success_result")
        stub = create_tool_stub(self.tool_def, mock_ipc)

        # Verify metadata is preserved
        self.assertEqual(stub.__name__, "test_tool")
        self.assertEqual(stub.__doc__, "A test tool for sandbox")

        # Execute the stub as if called by the sandboxed worker
        result = stub(target_file="app.py", limit=10)
        self.assertEqual(result, "ipc_success_result")
        mock_ipc.assert_called_once_with(
            "test_tool", {"target_file": "app.py", "limit": 10}
        )

    def test_a_positional_call_lands_on_the_schema_order(self) -> None:
        # `search_code("coth", file_pattern="hyperbolic.py")` is what a model
        # writes. Refusing it raised a TypeError naming an internal function,
        # which tells the model nothing it can act on: 117 steps out of 240 in
        # one campaign, and one model spent all thirty iterations there without
        # ever reaching `run_tests`.
        searcher = MCPToolDefinition(
            name="search_code",
            description="Search",
            input_schema={
                "type": "object",
                "properties": {"pattern": {}, "file_pattern": {}, "directory": {}},
            },
        )
        mock_ipc = MagicMock(return_value="found")
        stub = create_tool_stub(searcher, mock_ipc)

        assert stub("coth", file_pattern="hyperbolic.py") == "found"
        mock_ipc.assert_called_once_with(
            "search_code", {"pattern": "coth", "file_pattern": "hyperbolic.py"}
        )

    def test_the_same_argument_twice_is_named_rather_than_raised(self) -> None:
        searcher = MCPToolDefinition(
            name="search_code",
            description="Search",
            input_schema={"type": "object", "properties": {"pattern": {}}},
        )
        stub = create_tool_stub(searcher, MagicMock())

        answer = stub("coth", pattern="cotm")

        assert "pattern" in answer
        assert "twice" in answer

    def test_more_arguments_than_the_schema_has_says_so(self) -> None:
        stub = create_tool_stub(self.tool_def, MagicMock())

        answer = stub("one", "two")

        assert "at most 0 arguments" in answer

    def test_get_sandbox_tool_stubs(self) -> None:
        mock_ipc = MagicMock()
        stubs = get_sandbox_tool_stubs([self.tool_def], mock_ipc)

        self.assertIn("test_tool", stubs)
        self.assertTrue(callable(stubs["test_tool"]))

    async def test_execute_mcp_tool_call_success(self) -> None:
        mock_registry = MagicMock(spec=MCPToolRegistry)
        mock_tool_wrapper = AsyncMock(return_value="tool_executed_correctly")
        mock_registry.tools = {"test_tool": mock_tool_wrapper}

        # Parent process receives IPC request and executes it
        result = await execute_mcp_tool_call(
            mock_registry, "test_tool", {"arg1": "value1"}
        )

        self.assertEqual(result, "tool_executed_correctly")
        mock_tool_wrapper.assert_called_once_with(arg1="value1")

    async def test_execute_mcp_tool_call_not_found(self) -> None:
        mock_registry = MagicMock(spec=MCPToolRegistry)
        mock_registry.tools = {}

        result = await execute_mcp_tool_call(mock_registry, "unknown_tool", {})
        self.assertIn("not found in registry", result)

    async def test_execute_mcp_tool_call_type_error(self) -> None:
        # Simulates the agent providing the wrong parameters
        mock_registry = MagicMock(spec=MCPToolRegistry)
        mock_tool_wrapper = AsyncMock(
            side_effect=TypeError("missing 1 required argument")
        )
        mock_registry.tools = {"test_tool": mock_tool_wrapper}

        result = await execute_mcp_tool_call(mock_registry, "test_tool", {})
        self.assertIn("Invalid arguments", result)

    async def test_execute_mcp_tool_call_general_error(self) -> None:
        mock_registry = MagicMock(spec=MCPToolRegistry)
        mock_tool_wrapper = AsyncMock(side_effect=RuntimeError("connection lost"))
        mock_registry.tools = {"test_tool": mock_tool_wrapper}

        result = await execute_mcp_tool_call(mock_registry, "test_tool", {})
        self.assertIn("Tool execution failed", result)


if __name__ == "__main__":
    unittest.main()
