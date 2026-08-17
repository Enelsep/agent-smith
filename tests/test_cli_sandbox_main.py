"""The `sandbox` entry point: its flags, its configuration, its MCP session."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any

import pytest

from agent_smith.cli.sandbox import main as cli
from agent_smith.cli.sandbox.mcp_bridge import MCPBridge, MCPBridgeError
from agent_smith.config import ConfigError
from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.models.contract import SandboxConfig
from agent_smith.sandbox.process import Sandbox

if TYPE_CHECKING:
    from pathlib import Path

READ_FILE = MCPToolDefinition(
    name="read_file",
    description="Read a file from disk",
    input_schema={
        "type": "object",
        "properties": {"filepath": {"type": "string"}},
        "required": ["filepath"],
    },
)


class FakeMCPClient:
    """An MCP client that answers in-process, with no transport underneath."""

    def __init__(
        self,
        tools: list[MCPToolDefinition] | None = None,
        fail_on_connect: bool = False,
        cancel_on_connect: bool = False,
    ) -> None:
        self.tools = [READ_FILE] if tools is None else tools
        self.fail_on_connect = fail_on_connect
        self.cancel_on_connect = cancel_on_connect
        self.connected = False
        self.disconnected = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def connect(self) -> None:
        if self.cancel_on_connect:
            raise asyncio.CancelledError
        if self.fail_on_connect:
            raise ConnectionError("no server there")
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def list_tools(self) -> list[MCPToolDefinition]:
        return list(self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return f"contents of {arguments.get('filepath')}"


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------


def test_the_bare_invocation_names_no_config_and_no_server() -> None:
    args = cli.parse_args([])

    assert args.config is None
    assert args.mcp_stdio is None
    assert args.mcp_server is None


def test_the_config_file_is_positional_as_the_subject_writes_it() -> None:
    args = cli.parse_args(["sandbox_template.json"])

    assert args.config is not None
    assert args.config.name == "sandbox_template.json"


def test_a_stdio_command_and_a_config_file_can_be_given_together() -> None:
    args = cli.parse_args(
        ["--mcp-stdio", "python mcp_tools_mbpp.py", "sandbox_template.json"]
    )

    assert args.mcp_stdio == "python mcp_tools_mbpp.py"
    assert args.config is not None


def test_an_http_server_url_is_accepted() -> None:
    args = cli.parse_args(["--mcp-server", "http://127.0.0.1:8000/mcp"])

    assert args.mcp_server == "http://127.0.0.1:8000/mcp"


def test_the_two_transports_are_mutually_exclusive() -> None:
    # A session speaks to one MCP server or to none; accepting both would
    # leave which one wins up to argument order.
    with pytest.raises(SystemExit):
        cli.parse_args(["--mcp-stdio", "python x.py", "--mcp-server", "http://x"])


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_an_unnamed_config_falls_back_to_the_working_defaults() -> None:
    # `SandboxConfig()` leaves both allowlists empty, and `from_config` reads
    # that as "unset". A bare `uv run sandbox` must be a usable prompt, not one
    # that refuses every import.
    sandbox = Sandbox.from_config(cli.load_config(None))

    assert sandbox.authorized_imports == Sandbox.DEFAULT_IMPORTS
    assert sandbox.allowed_directories == Sandbox.DEFAULT_DIRECTORIES


def test_a_named_config_file_is_what_the_sandbox_enforces(tmp_path: Path) -> None:
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(
            {
                "authorized_imports": ["math"],
                "allowed_directories": ["/tmp/only-here"],
                "max_execution_time_seconds": 7,
                "max_memory_mb": 64,
            }
        ),
        encoding="utf-8",
    )

    sandbox = Sandbox.from_config(cli.load_config(path))

    assert sandbox.authorized_imports == ["math"]
    assert sandbox.allowed_directories == ["/tmp/only-here"]
    assert sandbox.timeout == 7
    assert sandbox.max_memory_mb == 64


def test_the_repository_template_loads(tmp_path: Path) -> None:
    # The subject's own example invocation names this file.
    from pathlib import Path as RealPath

    template = RealPath(__file__).resolve().parent.parent / "sandbox_template.json"

    assert cli.load_config(template).max_execution_time_seconds == 30


def test_an_absent_config_file_is_explained_not_raised_raw(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        cli.load_config(tmp_path / "nothing-here.json")


def test_a_bad_config_file_leaves_by_the_usage_door(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["sandbox", str(tmp_path / "absent.json")])

    with pytest.raises(SystemExit) as left:
        cli.main()

    assert left.value.code == cli.USAGE_EXIT
    assert "cannot read" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Building the MCP client
# --------------------------------------------------------------------------


def test_no_transport_flag_builds_no_client() -> None:
    assert cli.build_client(cli.parse_args([])) is None


def test_a_stdio_command_is_split_into_a_program_and_its_arguments() -> None:
    client = cli.build_client(cli.parse_args(["--mcp-stdio", "python mcp_tools.py"]))

    assert client is not None
    assert client.command == "python"  # type: ignore[attr-defined]
    assert client.args == ["mcp_tools.py"]  # type: ignore[attr-defined]


def test_a_quoted_path_in_the_stdio_command_survives_splitting() -> None:
    client = cli.build_client(
        cli.parse_args(["--mcp-stdio", 'python "my tools/server.py"'])
    )

    assert client is not None
    assert client.args == ["my tools/server.py"]  # type: ignore[attr-defined]


def test_an_empty_stdio_command_is_explained() -> None:
    with pytest.raises(ConfigError, match="needs a command"):
        cli.build_client(cli.parse_args(["--mcp-stdio", "   "]))


def test_an_unbalanced_quote_in_the_stdio_command_is_explained() -> None:
    with pytest.raises(ConfigError, match="not a valid command line"):
        cli.build_client(cli.parse_args(["--mcp-stdio", 'python "unclosed']))


def test_a_server_url_builds_an_http_client() -> None:
    client = cli.build_client(cli.parse_args(["--mcp-server", "http://host/mcp"]))

    assert client is not None
    assert client.url == "http://host/mcp"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# The banner
# --------------------------------------------------------------------------


def test_the_banner_quotes_the_limits_actually_enforced() -> None:
    sandbox = Sandbox(timeout=7.0, max_memory_mb=64, allowed_directories=["/testbed"])

    banner = cli.describe(sandbox, [])

    assert "7s" in banner
    assert "64 MB" in banner
    assert "/testbed" in banner


def test_the_banner_lists_final_answer_and_the_discovered_tools() -> None:
    banner = cli.describe(Sandbox(), [READ_FILE])

    assert "final_answer" in banner
    assert "read_file" in banner


def test_the_banner_says_how_to_leave() -> None:
    assert "exit" in cli.describe(Sandbox(), [])


def test_the_banner_carries_the_manual_for_the_connected_tools() -> None:
    banner = cli.describe(Sandbox(), [READ_FILE])

    # The signature, not just the name: what a person tries by hand here is
    # documented exactly as the model's prompt documents it.
    assert "## Tools" in banner
    assert "read_file(filepath: str)" in banner


def test_the_banner_carries_no_manual_when_no_server_is_connected() -> None:
    # Nothing is connected, so there is nothing to document and no reason to
    # spend four lines saying so at a prompt.
    assert "## Tools" not in cli.describe(Sandbox(), [])


# --------------------------------------------------------------------------
# The MCP bridge
# --------------------------------------------------------------------------


def test_the_bridge_discovers_the_servers_tools() -> None:
    bridge = MCPBridge(FakeMCPClient())
    bridge.start()
    try:
        assert [tool.name for tool in bridge.tool_defs] == ["read_file"]
    finally:
        bridge.close()


def test_the_bridge_builds_the_manual_when_it_connects() -> None:
    bridge = MCPBridge(FakeMCPClient())
    assert "No MCP server is connected" in bridge.manual  # before connecting

    bridge.start()
    try:
        assert "read_file(filepath: str)" in bridge.manual
    finally:
        bridge.close()


def test_a_different_server_produces_a_different_manual() -> None:
    # The manual is rebuilt from whatever `list_tools()` returned, so an
    # unknown server documents itself with no code change here.
    other = MCPToolDefinition(
        name="summon_kraken",
        description="Summon a kraken.",
        input_schema={
            "type": "object",
            "properties": {"depth": {"type": "number"}},
            "required": ["depth"],
        },
    )
    bridge = MCPBridge(FakeMCPClient(tools=[other]))
    bridge.start()
    try:
        assert "summon_kraken(depth: float)" in bridge.manual
        assert "read_file" not in bridge.manual
    finally:
        bridge.close()


def test_the_bridge_answers_a_call_synchronously() -> None:
    # This is the whole point: `Sandbox` calls its handler with the worker
    # blocked on the pipe, so the answer has to come back as a plain return.
    client = FakeMCPClient()
    bridge = MCPBridge(client)
    bridge.start()
    try:
        assert bridge.call("read_file", {"filepath": "a.py"}) == "contents of a.py"
    finally:
        bridge.close()

    assert client.calls == [("read_file", {"filepath": "a.py"})]


def test_the_bridge_disconnects_when_it_closes() -> None:
    client = FakeMCPClient()
    bridge = MCPBridge(client)
    bridge.start()
    bridge.close()

    assert client.disconnected is True


def test_a_server_that_will_not_connect_is_reported_not_swallowed() -> None:
    # Better to say so and stop than to open a prompt whose every tool fails
    # one at a time.
    bridge = MCPBridge(FakeMCPClient(fail_on_connect=True))

    with pytest.raises(MCPBridgeError, match="no server there"):
        bridge.start()


def test_a_cancelled_handshake_is_reported_like_any_other_failure() -> None:
    # An unreachable HTTP endpoint surfaces as a cancelled task rather than a
    # plain exception, and `CancelledError` derives from `BaseException`. Caught
    # with `except Exception` it slips past, the bridge reports itself ready
    # with no failure recorded, and the CLI opens a prompt whose tools cannot
    # work while the bridge thread dies printing its traceback.
    bridge = MCPBridge(FakeMCPClient(cancel_on_connect=True))

    with pytest.raises(MCPBridgeError, match="never completed the handshake"):
        bridge.start()


def test_calling_a_closed_bridge_returns_an_observation_not_an_exception() -> None:
    bridge = MCPBridge(FakeMCPClient())
    bridge.start()
    bridge.close()

    assert "not available" in bridge.call("read_file", {"filepath": "a.py"})


def test_an_unknown_tool_comes_back_as_an_observation() -> None:
    bridge = MCPBridge(FakeMCPClient())
    bridge.start()
    try:
        assert "not found" in bridge.call("nonexistent", {})
    finally:
        bridge.close()


def test_a_server_that_never_answers_gives_up_rather_than_hanging() -> None:
    import asyncio

    class Hanging(FakeMCPClient):
        async def connect(self) -> None:
            await asyncio.sleep(30)

    bridge = MCPBridge(Hanging(), startup_timeout=0.2)

    with pytest.raises(MCPBridgeError, match="did not answer"):
        bridge.start()


ECHO_SERVER = """
from mcp.server.fastmcp import FastMCP

server = FastMCP("probe")


@server.tool()
def shout(text: str) -> str:
    "Return TEXT in upper case."
    return text.upper()


if __name__ == "__main__":
    server.run()
"""


def test_a_real_stdio_server_survives_connect_call_and_disconnect(
    tmp_path: Path,
) -> None:
    # The reason this bridge owns a thread rather than reusing the caller's
    # loop: the stdio transport builds its task group inside the task that
    # connected it, and unwinding that group from a second task is what raises
    # anyio's "cancel scope in a different task". Only a real server exercises
    # it -- a fake client has no task group to mismatch.
    pytest.importorskip("mcp.server", reason="the MCP server API is not installed")

    from agent_smith.mcp.client import UnifiedMCPClient

    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER, encoding="utf-8")

    bridge = MCPBridge(UnifiedMCPClient(command=sys.executable, args=[str(script)]))
    bridge.start()
    try:
        assert [tool.name for tool in bridge.tool_defs] == ["shout"]
        assert bridge.call("shout", {"text": "hello"}) == "HELLO"
    finally:
        bridge.close()  # must not raise


def test_the_bridged_tools_reach_sandboxed_code_as_functions() -> None:
    # End to end over the real process boundary: a stub in the worker, a pipe,
    # the bridge's thread, the client, and the answer all the way back.
    client = FakeMCPClient()
    bridge = MCPBridge(client)
    bridge.start()
    try:
        config = SandboxConfig(max_execution_time_seconds=5)
        with Sandbox.from_config(
            config, tool_defs=bridge.tool_defs, tool_handler=bridge.call
        ) as sandbox:
            result = sandbox.execute("print(read_file(filepath='a.py'))")
    finally:
        bridge.close()

    assert "contents of a.py" in result.stdout


def test_a_stdio_server_is_spawned_with_the_environment_it_was_launched_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The SWE-bench server reads TESTBED_PATH to find the repository, and the
    # documented way to set it is on the sandbox command line. The transport's
    # own default inherits six variables and TESTBED_PATH is not among them.
    monkeypatch.setenv("TESTBED_PATH", "/somewhere/testbed")

    from agent_smith.mcp.client import UnifiedMCPClient

    args = cli.parse_args(["--mcp-stdio", "python mcp_tools_swebench.py"])
    client = cli.build_client(args)

    assert isinstance(client, UnifiedMCPClient)
    assert client.env is not None
    assert client.env["TESTBED_PATH"] == "/somewhere/testbed"
