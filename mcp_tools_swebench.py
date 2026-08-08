from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from agent_smith.tools.edit_file import edit_file
from agent_smith.tools.find_references import find_references
from agent_smith.tools.get_patch import get_patch
from agent_smith.tools.list_files import list_files
from agent_smith.tools.read_file import read_file
from agent_smith.tools.run_command import run_command
from agent_smith.tools.run_tests import run_tests
from agent_smith.tools.search_code import search_code
from agent_smith.tools.search_definition import (
    search_function_or_class_definition_in_code,
)


def get_testbed_path() -> str:
    """Returns the configured TESTBED_PATH or current working directory."""
    return os.environ.get("TESTBED_PATH", os.getcwd())


# The repository root is `/testbed` inside the container, and the model writes
# that prefix because the task statement and every listing show it. When the
# server runs outside a container, TESTBED_PATH points somewhere else and the
# same absolute paths have to land there instead.
TESTBED_ROOT = "/testbed"

# Every argument that carries a path. Rewriting them in one place at dispatch
# keeps each tool branch free of the concern, and covers tools added later.
PATH_ARGUMENTS = frozenset({"filepath", "directory", "workdir"})


def in_testbed(path: str) -> str:
    """Resolves a path written against the repository root."""
    testbed = get_testbed_path()
    if path == TESTBED_ROOT:
        return testbed
    if path.startswith(TESTBED_ROOT + "/"):
        return os.path.join(testbed, path[len(TESTBED_ROOT) + 1 :])
    return path


# ------------------------------------------------------------------------------
# Request Dispatcher
# ------------------------------------------------------------------------------
def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params", {}) or {}

    # Standard notifications do not require a response
    if req_id is None:
        return None

    result: dict[str, Any] = {}
    testbed = get_testbed_path()

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {
                "name": "swebench-tools-server",
                "version": "0.1.0",
            },
        }

    elif method == "ping":
        result = {}

    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read file contents with optional start and end line ranges.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                        },
                        "required": ["filepath"],
                    },
                },
                {
                    "name": "edit_file",
                    "description": "Replace precise occurrences of text within a file.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string"},
                            "old_str": {"type": "string"},
                            "new_str": {"type": "string"},
                        },
                        "required": ["filepath", "old_str", "new_str"],
                    },
                },
                {
                    "name": "list_files",
                    "description": "List files in a directory matching an optional fnmatch pattern.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string"},
                            "pattern": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "search_code",
                    "description": "Search code using regex pattern matching.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "file_pattern": {"type": "string"},
                        },
                        "required": ["pattern"],
                    },
                },
                {
                    "name": "search_function_or_class_definition_in_code",
                    "description": "Locate class or function definitions by name.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
                {
                    "name": "find_references",
                    "description": "Find symbol references across the repository.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "filepath": {"type": "string"},
                            "line": {"type": "integer"},
                        },
                        "required": ["name"],
                    },
                },
                {
                    "name": "run_tests",
                    "description": "Run repository tests using an evaluation script.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "eval_script": {"type": "string"},
                            "directory": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "get_patch",
                    "description": "Generate git diff patch for uncommitted changes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "run_command",
                    "description": "Execute arbitrary shell command in testbed workspace.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "workdir": {"type": "string"},
                        },
                        "required": ["command"],
                    },
                },
            ]
        }

    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        args = {
            key: in_testbed(value)
            if key in PATH_ARGUMENTS and isinstance(value, str)
            else value
            for key, value in args.items()
        }

        try:
            if name == "read_file":
                filepath = str(args.get("filepath", ""))
                rf_kwargs: dict[str, Any] = {}
                if "start_line" in args and args["start_line"] is not None:
                    rf_kwargs["start_line"] = int(args["start_line"])
                if "end_line" in args and args["end_line"] is not None:
                    rf_kwargs["end_line"] = int(args["end_line"])
                output = read_file(filepath, **rf_kwargs)

            elif name == "edit_file":
                filepath = str(args.get("filepath", ""))
                old_str = str(args.get("old_str", ""))
                new_str = str(args.get("new_str", ""))
                output = edit_file(filepath, old_str, new_str)

            elif name == "list_files":
                directory = str(args.get("directory", testbed))
                lf_kwargs: dict[str, Any] = {}
                if "pattern" in args and args["pattern"] is not None:
                    lf_kwargs["pattern"] = str(args["pattern"])
                output = list_files(directory=directory, **lf_kwargs)

            elif name == "search_code":
                pattern = str(args.get("pattern", ""))
                sc_kwargs: dict[str, Any] = {}
                if "file_pattern" in args and args["file_pattern"] is not None:
                    sc_kwargs["file_pattern"] = str(args["file_pattern"])
                # The three search tools take no directory from the model --
                # there is only one repository to search. They default to `.`,
                # which is the testbed only because the container runs us
                # there; naming it holds wherever the server is started.
                output = search_code(pattern, directory=testbed, **sc_kwargs)

            elif name == "search_function_or_class_definition_in_code":
                sym_name = str(args.get("name", ""))
                output = search_function_or_class_definition_in_code(
                    sym_name, directory=testbed
                )

            elif name == "find_references":
                sym_name = str(args.get("name", ""))
                fr_kwargs: dict[str, Any] = {}
                if "filepath" in args and args["filepath"] is not None:
                    fr_kwargs["filepath"] = str(args["filepath"])
                if "line" in args and args["line"] is not None:
                    fr_kwargs["line"] = int(args["line"])
                output = find_references(sym_name, directory=testbed, **fr_kwargs)

            elif name == "run_tests":
                directory = str(args.get("directory", testbed))
                rt_kwargs: dict[str, Any] = {}
                if "eval_script" in args and args["eval_script"] is not None:
                    rt_kwargs["eval_script"] = str(args["eval_script"])
                output = run_tests(directory=directory, **rt_kwargs)

            elif name == "get_patch":
                directory = str(args.get("directory", testbed))
                output = get_patch(directory=directory)

            elif name == "run_command":
                command = str(args.get("command", ""))
                workdir = str(args.get("workdir", testbed))
                output = run_command(command, workdir=workdir)

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                }

        except (ValueError, TypeError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": f"Invalid arguments for tool '{name}': {exc}",
                },
            }

        result = {
            "content": [
                {
                    "type": "text",
                    "text": str(output),
                }
            ]
        }

    elif method == "resources/list":
        result = {
            "resources": [
                {
                    "uri": "swebench://guidelines",
                    "name": "SWE-bench Guidelines",
                    "description": "Guidelines for resolving SWE-bench tasks.",
                    "mimeType": "text/plain",
                }
            ]
        }

    elif method == "resources/read":
        uri = str(params.get("uri", ""))
        if uri == "swebench://guidelines":
            result = {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": (
                            "SWE-bench Guidelines:\n"
                            "1. Inspect testbed environment and locate repo files.\n"
                            "2. Reproduce failure using `run_tests` or `run_command`.\n"
                            "3. Edit files precisely with `edit_file` and check diff using `get_patch`."
                        ),
                    }
                ]
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Unknown resource: {uri}"},
            }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


# ------------------------------------------------------------------------------
# Stdio Loop
# ------------------------------------------------------------------------------
async def main() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break

        raw = line.decode("utf-8").strip()
        if not raw:
            continue

        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            parse_error = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(parse_error) + "\n")
            sys.stdout.flush()
            continue

        if isinstance(request, dict):
            try:
                response = _handle_request(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except Exception as exc:  # noqa: BLE001
                req_id = request.get("id")
                if req_id is not None:
                    internal_error = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32603,
                            "message": f"Internal error: {exc}",
                        },
                    }
                    sys.stdout.write(json.dumps(internal_error) + "\n")
                    sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
