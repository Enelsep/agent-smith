"""MCP tools reach sandboxed code as plain callables, run on the parent side."""

from __future__ import annotations

import time

from agent_smith.mcp.protocol import MCPToolDefinition
from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

READ_FILE = MCPToolDefinition(
    name="read_file",
    description="Read a file from disk",
    input_schema={
        "type": "object",
        "properties": {"filepath": {"type": "string"}},
        "required": ["filepath"],
    },
)


def test_tool_call_round_trips_through_the_pipe() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(name: str, arguments: dict[str, object]) -> str:
        seen.append((name, arguments))
        return f"contents of {arguments['filepath']}"

    with Sandbox(timeout=5.0, tool_defs=[READ_FILE], tool_handler=handler) as sb:
        r = sb.execute("out = read_file(filepath='a.py')\nprint(out)")
        assert r.outcome is Outcome.OK
        assert "contents of a.py" in r.stdout

    # The tool ran in the parent process, not in the sandbox.
    assert seen == [("read_file", {"filepath": "a.py"})]


def test_tool_result_is_usable_by_later_code_in_the_namespace() -> None:
    with Sandbox(
        timeout=5.0,
        tool_defs=[READ_FILE],
        tool_handler=lambda name, args: "line one",
    ) as sb:
        assert sb.execute("data = read_file(filepath='x')").outcome is Outcome.OK
        r = sb.execute("print(data.upper())")
        assert r.outcome is Outcome.OK
        assert "LINE ONE" in r.stdout


def test_failing_tool_becomes_an_observation_not_a_crash() -> None:
    def handler(name: str, arguments: dict[str, object]) -> str:
        raise RuntimeError("server went away")

    with Sandbox(timeout=5.0, tool_defs=[READ_FILE], tool_handler=handler) as sb:
        r = sb.execute("print(read_file(filepath='a.py'))")
        assert r.outcome is Outcome.OK
        assert "failed" in r.stdout
        assert "server went away" in r.stdout


def test_tool_without_a_connected_server_explains_itself() -> None:
    with Sandbox(timeout=5.0, tool_defs=[READ_FILE]) as sb:
        r = sb.execute("print(read_file(filepath='a.py'))")
        assert r.outcome is Outcome.OK
        assert "no MCP server is connected" in r.stdout


def test_slow_tool_does_not_consume_the_execution_timeout() -> None:
    # The subject is explicit: MCP server work is not subject to the sandbox
    # timeout. A 2s tool under a 1s timeout must still succeed.
    def slow(name: str, arguments: dict[str, object]) -> str:
        time.sleep(2.0)
        return "done"

    with Sandbox(timeout=1.0, tool_defs=[READ_FILE], tool_handler=slow) as sb:
        r = sb.execute("print(read_file(filepath='a.py'))")
        assert r.outcome is Outcome.OK
        assert "done" in r.stdout
        assert sb.restarts == 0


def test_timer_resumes_after_a_tool_call() -> None:
    # Pausing the clock for the tool must not disarm it: code that loops
    # forever after the call still hits the soft timeout.
    with Sandbox(
        timeout=1.0,
        tool_defs=[READ_FILE],
        tool_handler=lambda name, args: "ok",
    ) as sb:
        r = sb.execute("read_file(filepath='a.py')\nwhile True:\n    pass\n")
        assert r.outcome is Outcome.SOFT_TIMEOUT


def test_final_answer_still_present_alongside_tools() -> None:
    with Sandbox(
        timeout=5.0,
        tool_defs=[READ_FILE],
        tool_handler=lambda name, args: "x",
    ) as sb:
        r = sb.execute("final_answer('done')")
        assert r.outcome is Outcome.FINAL_ANSWER
        assert r.final_answer == "done"


def test_no_tool_stubs_when_no_server_is_configured() -> None:
    with Sandbox(timeout=5.0) as sb:
        r = sb.execute("read_file(filepath='a.py')")
        assert r.outcome is Outcome.ERROR
        assert r.error is not None
        assert "NameError" in r.error
