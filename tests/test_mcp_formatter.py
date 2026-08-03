"""
Unit tests for MCP-3 Tool Schema Formatter.
"""

import unittest

from agent_smith.mcp.formatter import (
    to_anthropic_tool,
    to_anthropic_tools,
    to_openai_tool,
    to_openai_tools,
    to_react_prompt_string,
)
from agent_smith.mcp.protocol import MCPToolDefinition


class TestMCPFormatter(unittest.TestCase):
    def setUp(self) -> None:
        self.tool1 = MCPToolDefinition(
            name="read_file",
            description="Reads content from a file",
            input_schema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Target file path"}
                },
                "required": ["filepath"],
            },
        )
        self.tool2 = MCPToolDefinition(
            name="search_code",
            description="Searches code patterns in repository",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search regex"},
                    "limit": {"type": "integer", "description": "Max results"},
                },
                "required": ["query"],
            },
        )
        self.tools = [self.tool1, self.tool2]

    def test_to_openai_tool(self) -> None:
        formatted = to_openai_tool(self.tool1)
        self.assertEqual(formatted["type"], "function")
        self.assertEqual(formatted["function"]["name"], "read_file")
        self.assertEqual(
            formatted["function"]["description"], "Reads content from a file"
        )
        self.assertIn("filepath", formatted["function"]["parameters"]["properties"])

    def test_to_openai_tools(self) -> None:
        formatted_list = to_openai_tools(self.tools)
        self.assertEqual(len(formatted_list), 2)
        self.assertEqual(formatted_list[0]["function"]["name"], "read_file")
        self.assertEqual(formatted_list[1]["function"]["name"], "search_code")

    def test_to_anthropic_tool(self) -> None:
        formatted = to_anthropic_tool(self.tool1)
        self.assertEqual(formatted["name"], "read_file")
        self.assertEqual(formatted["description"], "Reads content from a file")
        self.assertIn("filepath", formatted["input_schema"]["properties"])

    def test_to_anthropic_tools(self) -> None:
        formatted_list = to_anthropic_tools(self.tools)
        self.assertEqual(len(formatted_list), 2)
        self.assertEqual(formatted_list[0]["name"], "read_file")
        self.assertEqual(formatted_list[1]["name"], "search_code")

    def test_to_react_prompt_string(self) -> None:
        prompt_str = to_react_prompt_string(self.tools)
        self.assertIn("Available Tools:", prompt_str)
        self.assertIn("Tool: read_file", prompt_str)
        self.assertIn("Tool: search_code", prompt_str)
        self.assertIn("* filepath (string, required)", prompt_str)

    def test_to_react_prompt_string_empty(self) -> None:
        prompt_str = to_react_prompt_string([])
        self.assertEqual(prompt_str, "No tools available.")

    def test_to_openai_tool_empty_schema(self) -> None:
        """Test that an empty input schema gets normalized for OpenAI."""
        tool_empty = MCPToolDefinition(
            name="ping",
            description="Ping the agent",
            input_schema={},
        )
        formatted = to_openai_tool(tool_empty)
        # Verify the normalization occurred
        self.assertEqual(formatted["function"]["parameters"]["type"], "object")
        self.assertEqual(formatted["function"]["parameters"]["properties"], {})


if __name__ == "__main__":
    unittest.main()
