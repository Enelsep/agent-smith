"""
Unit tests for MCP-2: Dynamic Tool Wrappers, Registry, and PR #17 fixes.
"""

import inspect
import unittest
from typing import Any
from unittest.mock import AsyncMock

from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition
from agent_smith.mcp.registry import MCPToolRegistry
from agent_smith.mcp.wrapper import build_inspect_signature, build_mcp_tool_wrapper


class DummyMCPClient(MCPClientProtocol):
    """Mock client to validate registry and wrapper behaviors."""

    def __init__(self) -> None:
        self.call_history: list[tuple[str, dict[str, Any]]] = []

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def list_tools(self) -> list[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="read_file",
                description="Read a file from disk",
                input_schema={
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "File path"}
                    },
                    "required": ["filepath"],
                },
            ),
            MCPToolDefinition(
                name="edit_file",
                description="Edit lines in a file",
                input_schema={
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "File path"},
                        "start_line": {"type": "integer", "description": "Start line"},
                        "content": {"type": "string", "description": "New content"},
                    },
                    "required": ["filepath", "content"],
                },
            ),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.call_history.append((name, arguments))
        return f"Result for {name}: {arguments}"


class TestMCPWrapperAndRegistry(unittest.IsolatedAsyncioTestCase):
    async def test_inspect_signature_building(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["path"],
        }
        sig, _alias_map = build_inspect_signature(schema)
        self.assertIn("path", sig.parameters)
        self.assertIn("count", sig.parameters)
        self.assertEqual(sig.parameters["path"].default, inspect.Parameter.empty)
        self.assertIsNone(sig.parameters["count"].default)

    async def test_successful_tool_execution(self) -> None:
        mock_client = AsyncMock(spec=MCPClientProtocol)
        mock_client.call_tool.return_value = "File updated successfully"

        tool_def = MCPToolDefinition(
            name="edit",
            description="Editor",
            input_schema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "line": {"type": "integer"},
                },
                "required": ["filepath"],
            },
        )

        wrapper = build_mcp_tool_wrapper(mock_client, tool_def)
        result = await wrapper("main.py", line=10)

        self.assertEqual(result, "File updated successfully")
        mock_client.call_tool.assert_called_once_with(
            "edit", {"filepath": "main.py", "line": 10}
        )

    async def test_registry_discovery_and_invocation(self) -> None:
        client = DummyMCPClient()
        registry = MCPToolRegistry(client)

        tools = await registry.discover_tools()

        self.assertIn("read_file", tools)
        self.assertIn("edit_file", tools)
        read_res = await tools["read_file"](filepath="config.json")
        self.assertIn("config.json", read_res)
        edit_res = await registry.call_tool_by_name(
            "edit_file", filepath="app.py", content="print('hello')"
        )
        self.assertIn("app.py", edit_res)
        self.assertEqual(len(client.call_history), 2)


class TestPR17Fixes(unittest.IsolatedAsyncioTestCase):
    async def test_issue_1_per_tool_isolation(self) -> None:
        client = AsyncMock(spec=MCPClientProtocol)
        good1 = MCPToolDefinition(name="good1", description="1", input_schema={})
        bad = MCPToolDefinition(
            name="bad", description="2", input_schema={"properties": "not-a-dict"}
        )
        good2 = MCPToolDefinition(name="good2", description="3", input_schema={})

        client.list_tools.return_value = [good1, bad, good2]
        registry = MCPToolRegistry(client)

        await registry.discover_tools()

        self.assertIn("good1", registry.tools)
        self.assertIn("good2", registry.tools)
        self.assertNotIn("bad", registry.tools)

    async def test_issue_2_kebab_case_param(self) -> None:
        client = AsyncMock(spec=MCPClientProtocol)
        client.call_tool.return_value = "ok"

        tool_def = MCPToolDefinition(
            name="read-file",
            description="read",
            input_schema={
                "type": "object",
                "properties": {
                    "start-line": {"type": "integer"},
                    "from": {"type": "string"},
                },
                "required": ["start-line"],
            },
        )

        wrapper = build_mcp_tool_wrapper(client, tool_def)
        res = await wrapper(start_line=10, from_="source")

        self.assertEqual(res, "ok")
        client.call_tool.assert_called_once_with(
            "read-file", {"start-line": 10, "from": "source"}
        )

    async def test_issue_3_omitted_optional_args_not_sent_as_null(self) -> None:
        client = AsyncMock(spec=MCPClientProtocol)
        client.call_tool.return_value = "ok"

        tool_def = MCPToolDefinition(
            name="edit",
            description="edit",
            input_schema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "optional_param": {"type": "integer"},
                },
                "required": ["filepath"],
            },
        )

        wrapper = build_mcp_tool_wrapper(client, tool_def)
        await wrapper(filepath="main.py")

        client.call_tool.assert_called_once_with("edit", {"filepath": "main.py"})


if __name__ == "__main__":
    unittest.main()
