"""
Unit tests for MCP-2: Dynamic Tool Wrappers and Registry.
"""

import inspect
import unittest
from typing import Any
from unittest.mock import AsyncMock

from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition
from agent_smith.mcp.registry import MCPToolRegistry
from agent_smith.mcp.wrapper import create_tool_wrapper, json_schema_to_pydantic_model


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
    async def test_pydantic_schema_generation(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
        }
        Model = json_schema_to_pydantic_model("TestModel", schema)
        instance = Model(name="Bob", age=30, tags=["admin", "user"])
        data = instance.model_dump()
        self.assertEqual(data["name"], "Bob")
        self.assertEqual(data["age"], 30)

    async def test_inspect_signature_and_docstrings(self) -> None:
        async def dummy_call(name: str, args: dict[str, Any]) -> str:
            return "ok"

        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The path"},
                "count": {"type": "integer", "description": "The count"},
            },
            "required": ["path"],
        }

        wrapper = create_tool_wrapper(
            tool_name="test_tool",
            description="Test tool",
            input_schema=schema,
            call_tool_fn=dummy_call,
        )

        sig = inspect.signature(wrapper)
        self.assertIn("path", sig.parameters)
        self.assertIn("count", sig.parameters)
        self.assertEqual(sig.parameters["path"].annotation, str)
        self.assertEqual(sig.parameters["count"].default, None)
        doc = wrapper.__doc__
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertIn("Parameters:", doc)

    async def test_fast_fail_validation_error(self) -> None:
        mock_call = AsyncMock()

        schema = {
            "type": "object",
            "properties": {"line_number": {"type": "integer"}},
            "required": ["line_number"],
        }

        wrapper = create_tool_wrapper(
            tool_name="num_tool",
            description="Numeric tool",
            input_schema=schema,
            call_tool_fn=mock_call,
        )
        res_missing = await wrapper()
        self.assertIn("Observation: Invalid arguments", res_missing)
        mock_call.assert_not_called()
        res_bad_type = await wrapper(line_number="not_a_number")
        self.assertIn("Observation: Parameter validation failed", res_bad_type)
        mock_call.assert_not_called()

    async def test_successful_tool_execution(self) -> None:
        mock_call = AsyncMock(return_value="File updated successfully")

        schema = {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "line": {"type": "integer"},
            },
            "required": ["filepath"],
        }

        wrapper = create_tool_wrapper(
            tool_name="edit",
            description="Editor",
            input_schema=schema,
            call_tool_fn=mock_call,
        )

        result = await wrapper("main.py", line=10)
        self.assertEqual(result, "File updated successfully")
        mock_call.assert_called_once_with("edit", {"filepath": "main.py", "line": 10})

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


if __name__ == "__main__":
    unittest.main()
