from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from mcp_tools_swebench import _handle_request, in_testbed

MANDATORY = {
    "read_file",
    "edit_file",
    "list_files",
    "search_code",
    "search_function_or_class_definition_in_code",
    "find_references",
    "run_tests",
    "get_patch",
    "run_command",
}


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
    # The nine the subject makes mandatory, by name rather than by count: extra
    # tools are explicitly allowed, so a count would fail on a permitted change
    # while a missing mandatory one would slip through.
    assert MANDATORY <= set(tool_names)


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


def test_the_server_runs_on_the_copied_package_alone(tmp_path: Path) -> None:
    # SWE-3 copies this file and `agent_smith` into the image and runs them
    # with PYTHONPATH. Nothing of ours is installed there, so an import the
    # copy does not carry is a run that dies before its first tool call.
    # `-S` drops site-packages, which is what stands in for the container:
    # without it the installed copy answers and the probe proves nothing.
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "src" / "agent_smith", tmp_path / "agent_smith")

    listed = subprocess.run(
        [sys.executable, "-S", str(root / "mcp_tools_swebench.py")],
        input='{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n',
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        check=False,
    )

    assert listed.returncode == 0, listed.stderr
    assert '"read_file"' in listed.stdout


def test_an_unparsable_line_is_skipped_rather_than_answered(tmp_path: Path) -> None:
    # JSON-RPC answers a parse error with a null id; the MCP client's schema
    # rejects a null id, its reader dies on the validation error, and the
    # bridge hangs with no one left to answer. Measured: a campaign stopped for
    # 25 minutes on one task. A line we cannot attribute gets no reply.
    root = Path(__file__).resolve().parents[1]
    served = subprocess.run(
        [sys.executable, str(root / "mcp_tools_swebench.py")],
        input='{ this is not json\n{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n',
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )

    replies = [line for line in served.stdout.splitlines() if line.strip()]
    assert len(replies) == 1, served.stdout
    assert json.loads(replies[0])["id"] == 1
    assert "unparsable" in served.stderr


def test_an_absolute_testbed_path_lands_in_the_configured_testbed(
    tmp_path: Path,
) -> None:
    # The model writes `/testbed/utils.py` because that is where the repository
    # sits in the container, and the statement shows it that way. Run outside a
    # container -- the sandbox exam does exactly this -- the same string has to
    # reach TESTBED_PATH rather than a root directory that does not exist.
    (tmp_path / "utils.py").write_text("def calculate_sum(): ...\n", encoding="utf-8")

    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "list_files",
            "arguments": {"directory": "/testbed", "pattern": "*.py"},
        },
    }

    with patch.dict(os.environ, {"TESTBED_PATH": str(tmp_path)}):
        resp = _handle_request(req)

    assert resp is not None
    assert "utils.py" in resp["result"]["content"][0]["text"]


def test_a_path_outside_the_testbed_root_is_left_alone(tmp_path: Path) -> None:
    # Only the `/testbed` prefix is ours to rewrite. `/tmp/agent` is a real
    # location the sandbox policy allows, and mapping it would break it.
    assert in_testbed("/tmp/agent/scratch.py") == "/tmp/agent/scratch.py"

    with patch.dict(os.environ, {"TESTBED_PATH": str(tmp_path)}):
        assert in_testbed("/testbed") == str(tmp_path)
        assert in_testbed("/testbed/pkg/mod.py") == str(tmp_path / "pkg" / "mod.py")


def test_the_search_tools_look_in_the_testbed_not_the_working_directory(
    tmp_path: Path,
) -> None:
    # They take no directory from the model: there is one repository to search.
    # Their own default is `.`, which is the testbed only while the container
    # happens to run the server there.
    (tmp_path / "utils.py").write_text(
        "def calculate_sum(values):\n    return sum(values)\n", encoding="utf-8"
    )

    def call(tool: str, arguments: dict[str, object]) -> str:
        request = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        with patch.dict(os.environ, {"TESTBED_PATH": str(tmp_path)}):
            resp = _handle_request(request)
        assert resp is not None
        return str(resp["result"]["content"][0]["text"])

    assert "utils.py" in call("search_code", {"pattern": "calculate_sum"})
    assert "utils.py" in call(
        "search_function_or_class_definition_in_code", {"name": "calculate_sum"}
    )


def test_the_two_extra_tools_are_offered_beside_the_nine_mandatory_ones() -> None:
    # The subject fixes nine and says "you may implement any additional tools
    # you consider useful". These two each answer a failure we measured.
    resp = _handle_request({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})

    assert resp is not None
    names = [tool["name"] for tool in resp["result"]["tools"]]
    assert "search_code_with_context" in names
    assert "write_file" in names
    assert MANDATORY <= set(names)


def test_a_context_search_runs_against_the_testbed(tmp_path: Path) -> None:
    (tmp_path / "utils.py").write_text(
        "import re\n\n\ndef calculate_sum(values):\n    return sum(values)\n",
        encoding="utf-8",
    )
    req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "search_code_with_context",
            "arguments": {"pattern": "calculate_sum", "context_lines": 1},
        },
    }

    with patch.dict(os.environ, {"TESTBED_PATH": str(tmp_path)}):
        resp = _handle_request(req)

    assert resp is not None
    text = resp["result"]["content"][0]["text"]
    # The matching line marked, and the line under it carried along: that
    # second line is the round trip this tool exists to save.
    assert "4> def calculate_sum(values):" in text
    assert "return sum(values)" in text


def test_run_tests_uses_the_script_the_harness_left_when_given_none(
    tmp_path: Path,
) -> None:
    # `run_tests()` with no argument is the whole point: a model asked for two
    # thousand characters of bash gets it wrong in every way bash allows. One
    # dropped a heredoc, one passed `/bin/bash` and stalled the tool for a full
    # timeout, one truncated it past the output markers and was handed a
    # verdict by accident.
    script = tmp_path / "eval_script.sh"
    script.write_text("echo '7 passed'\n", encoding="utf-8")

    req = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "run_tests", "arguments": {}},
    }

    with patch.dict(
        os.environ, {"EVAL_SCRIPT_PATH": str(script), "TESTBED_PATH": str(tmp_path)}
    ):
        resp = _handle_request(req)

    assert resp is not None
    assert "7 passed, 0 failed" in resp["result"]["content"][0]["text"]


def test_a_script_the_model_does_pass_still_wins(tmp_path: Path) -> None:
    # The argument stays accepted. A model that wants to run one file rather
    # than the whole suite is doing something reasonable, and the harness's own
    # verdict is taken separately.
    script = tmp_path / "eval_script.sh"
    script.write_text("echo '7 passed'\n", encoding="utf-8")

    req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "run_tests",
            "arguments": {"eval_script": "echo '2 passed'"},
        },
    }

    with patch.dict(
        os.environ, {"EVAL_SCRIPT_PATH": str(script), "TESTBED_PATH": str(tmp_path)}
    ):
        resp = _handle_request(req)

    assert resp is not None
    assert "2 passed, 0 failed" in resp["result"]["content"][0]["text"]
