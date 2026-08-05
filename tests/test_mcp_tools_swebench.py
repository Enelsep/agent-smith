from __future__ import annotations

from unittest.mock import patch

from mcp_tools_swebench import _handle_request


def test_initialize() -> None:
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = _handle_request(req)
    assert resp is not None
    assert resp["result"]["serverInfo"]["name"] == "swebench-tools-server"


def test_tools_list() -> None:
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = _handle_request(req)
    assert resp is not None
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert len(tool_names) == 9
    assert "read_file" in tool_names
    assert "edit_file" in tool_names
    assert "run_command" in tool_names


def test_tools_call_run_command() -> None:
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "run_command",
            "arguments": {"command": "echo 'hello'"},
        },
    }
    with patch("mcp_tools_swebench.run_command", return_value="Exit Code: 0\nhello"):
        resp = _handle_request(req)
        assert resp is not None
        assert "result" in resp
        content = resp["result"]["content"][0]["text"]
        assert "hello" in content


def test_tools_call_invalid_params() -> None:
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "read_file",
            "arguments": {"filepath": "foo.txt", "start_line": "invalid_number"},
        },
    }
    resp = _handle_request(req)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602
