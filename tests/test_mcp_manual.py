"""The sandbox manual: whatever the connected server publishes, compactly.

The subject asks for tool names, descriptions and parameter types, generated
from the server's own schemas so that connecting a different -- or unknown --
server documents it with no code change. It is re-sent to the model on every
turn, so what these pin as hard as the content is the size.
"""

from __future__ import annotations

from agent_smith.mcp.formatter import (
    MANUAL_HEADING,
    SUMMARY_LIMIT,
    to_sandbox_manual,
)
from agent_smith.mcp.protocol import MCPToolDefinition

READ_FILE = MCPToolDefinition(
    name="read_file",
    description="Read the content of a file with line numbers.",
    input_schema={
        "type": "object",
        "properties": {
            "filepath": {"type": "string", "description": "Target file"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "required": ["filepath"],
    },
)

RUN_TESTS = MCPToolDefinition(
    name="run_tests",
    description="Execute the evaluation script.",
    input_schema={"type": "object", "properties": {}},
)


def test_the_manual_names_the_tool_its_types_and_its_description() -> None:
    manual = to_sandbox_manual([READ_FILE])

    assert MANUAL_HEADING in manual
    assert "read_file(" in manual
    assert "filepath: str" in manual
    assert "start_line: int" in manual
    assert "Read the content of a file with line numbers." in manual


def test_required_parameters_come_first_and_optional_ones_show_a_default() -> None:
    # The order and the defaults have to match the wrapper the sandbox really
    # builds, or the manual documents a call that does not exist.
    line = _tool_line(to_sandbox_manual([READ_FILE]))

    assert line.index("filepath") < line.index("start_line")
    assert "start_line: int = None" in line
    assert "filepath: str = None" not in line


def test_a_tool_taking_nothing_renders_empty_parentheses() -> None:
    assert "run_tests()" in to_sandbox_manual([RUN_TESTS])


def test_schema_parameter_names_are_shown_as_python_accepts_them() -> None:
    # A server offering `start-line` is called as `start_line=`, because that
    # is what the generated wrapper binds. Quoting the raw name would document
    # a call that raises.
    kebab = MCPToolDefinition(
        name="read-file",
        description="Read a file.",
        input_schema={
            "type": "object",
            "properties": {
                "start-line": {"type": "integer"},
                "from": {"type": "string"},
            },
            "required": ["start-line"],
        },
    )

    manual = to_sandbox_manual([kebab])

    assert "start_line: int" in manual
    assert "start-line" not in manual
    assert "from_" in manual  # `from` is a keyword; the wrapper suffixes it


def test_an_unknown_server_is_documented_from_its_schemas_alone() -> None:
    # Nothing about this tool is known to us, which is the case the subject
    # cares about: the manual is whatever list_tools() said.
    invented = MCPToolDefinition(
        name="summon_kraken",
        description="Summon a kraken.",
        input_schema={
            "type": "object",
            "properties": {"depth": {"type": "number"}, "angry": {"type": "boolean"}},
            "required": ["depth"],
        },
    )

    manual = to_sandbox_manual([invented])

    assert "summon_kraken(depth: float, angry: bool = None)" in manual
    assert "Summon a kraken." in manual


def test_every_tool_takes_exactly_one_line() -> None:
    # Compactness is the requirement: this text is re-sent every turn against a
    # cumulative input budget.
    manual = to_sandbox_manual([READ_FILE, RUN_TESTS])
    entries = [line for line in manual.splitlines() if line.startswith("- ")]

    assert len(entries) == 2


def test_a_rambling_description_is_cut_to_one_line() -> None:
    windy = MCPToolDefinition(
        name="verbose",
        description="Word " * 200,
        input_schema={"type": "object", "properties": {}},
    )

    line = _tool_line(to_sandbox_manual([windy]))

    assert len(line) < SUMMARY_LIMIT + 60
    assert line.endswith("...")


def test_a_multi_line_description_is_collapsed() -> None:
    multi = MCPToolDefinition(
        name="t",
        description="First line.\n\n    Indented second line.",
        input_schema={"type": "object", "properties": {}},
    )

    manual = to_sandbox_manual([multi])

    assert "First line. Indented second line." in manual
    assert len([line for line in manual.splitlines() if line.startswith("- ")]) == 1


def test_an_unknown_json_type_is_left_unannotated_rather_than_guessed() -> None:
    odd = MCPToolDefinition(
        name="t",
        description="",
        input_schema={
            "type": "object",
            "properties": {"thing": {"anyOf": [{"type": "string"}]}},
            "required": ["thing"],
        },
    )

    assert "t(thing)" in to_sandbox_manual([odd])


def test_a_nullable_type_uses_the_non_null_half() -> None:
    nullable = MCPToolDefinition(
        name="t",
        description="",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": ["string", "null"]}},
            "required": ["name"],
        },
    )

    assert "name: str" in to_sandbox_manual([nullable])


def test_no_server_says_so_instead_of_rendering_an_empty_list() -> None:
    manual = to_sandbox_manual([])

    assert "No MCP server is connected" in manual
    assert MANUAL_HEADING in manual


def test_a_malformed_schema_does_not_raise() -> None:
    # An unknown server can publish anything at all; a manual that raises
    # would take down a session over documentation.
    junk = MCPToolDefinition(
        name="t", description="d", input_schema={"properties": "not-a-dict"}
    )

    assert "t()" in to_sandbox_manual([junk])


def _tool_line(manual: str) -> str:
    """The single `- ...` entry in a one-tool manual."""
    entries = [line for line in manual.splitlines() if line.startswith("- ")]
    assert len(entries) == 1, entries
    return entries[0]
