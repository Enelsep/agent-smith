from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from typing import Any


def _run_tests(
    code: str,
    test_list: list[str],
    test_imports: list[str] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    if not test_list:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "error": "No test cases provided.",
        }

    imports_block = "\n".join(test_imports) if test_imports else ""
    tests_block = "\n".join(test_list)

    script_parts = []
    if imports_block:
        script_parts.append(f"# --- IMPORTS ---\n{imports_block}")
    script_parts.append(code)
    script_parts.append(f"# --- TEST CASES ---\n{tests_block}")

    script_content = "\n\n".join(script_parts) + "\n"

    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = os.path.join(tmp_dir, "test_runner.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)

        try:
            res = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "success": res.returncode == 0,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "error": None
                if res.returncode == 0
                else (res.stderr or res.stdout or "Test assertions failed."),
            }

        except subprocess.TimeoutExpired as exc:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "error": f"Execution timed out after {timeout} seconds.",
            }

        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "error": f"Failed to execute test subprocess: {exc}",
            }


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params", {}) or {}

    if req_id is None:
        return None

    result: dict[str, Any] = {}

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {
                "name": "mbpp-tools-server",
                "version": "0.1.0",
            },
        }

    elif method == "ping":
        result = {}

    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "run_tests",
                    "description": (
                        "Executes Python code with assertion test cases in an"
                        " isolated subprocess."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python source code / solution functions.",
                            },
                            "test_list": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "List of assertion statements (e.g., ['assert solution(1) == 2'])."
                                ),
                            },
                            "test_imports": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Optional list of import statements required for tests."
                                ),
                            },
                            "timeout": {
                                "type": "number",
                                "description": (
                                    "Maximum execution duration in seconds"
                                    " (default: 5.0)."
                                ),
                                "default": 5.0,
                            },
                        },
                        "required": ["code", "test_list"],
                    },
                }
            ]
        }

    elif method == "tools/call":
        name = params.get("name")
        if name != "run_tests":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }

        args = params.get("arguments", {}) or {}
        code = str(args.get("code", ""))

        raw_test_list = args.get("test_list")
        if not isinstance(raw_test_list, list):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": "Invalid params: 'test_list' must be a list of strings",
                },
            }
        test_list = [str(x) for x in raw_test_list]

        raw_test_imports = args.get("test_imports", [])
        if isinstance(raw_test_imports, list):
            test_imports = [str(x) for x in raw_test_imports]
        else:
            test_imports = [str(raw_test_imports)] if raw_test_imports else []

        raw_timeout = args.get("timeout", 5.0)
        try:
            timeout = float(raw_timeout)
        except (ValueError, TypeError):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": f"Invalid params: 'timeout' must be a valid number, got {raw_timeout!r}",
                },
            }

        outcome = _run_tests(code, test_list, test_imports, timeout)
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(outcome),
                }
            ]
        }

    elif method == "resources/list":
        result = {
            "resources": [
                {
                    "uri": "mbpp://guidelines",
                    "name": "MBPP Guidelines",
                    "description": "Guidelines for solving MBPP tasks.",
                    "mimeType": "text/plain",
                }
            ]
        }

    elif method == "resources/read":
        uri = str(params.get("uri", ""))
        if uri == "mbpp://guidelines":
            result = {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": (
                            "MBPP Resolution Guidelines:\n"
                            "1. Every solution must include all required imports.\n"
                            "2. Always validate your code using `run_tests` before calling `final_answer`.\n"
                            "3. Do not rely on third-party libraries outside the standard library."
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

    elif method == "prompts/list":
        result = {
            "prompts": [
                {
                    "name": "solve_mbpp_task",
                    "description": "MCP Prompt template to guide the LLM on an MBPP problem.",
                    "arguments": [
                        {
                            "name": "task_description",
                            "description": "Description of the MBPP task",
                            "required": True,
                        },
                        {
                            "name": "test_list",
                            "description": "List of test cases for the MBPP task",
                            "required": True,
                        },
                        {
                            "name": "test_imports",
                            "description": "List of required imports for the MBPP task",
                            "required": False,
                        },
                    ],
                }
            ]
        }

    elif method == "prompts/get":
        name = params.get("name")
        if name != "solve_mbpp_task":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown prompt: {name}"},
            }

        args = params.get("arguments", {}) or {}
        task_description = str(args.get("task_description", ""))

        raw_test_list = args.get("test_list", [])
        if isinstance(raw_test_list, list):
            test_list = [str(t) for t in raw_test_list]
        else:
            test_list = [str(raw_test_list)] if raw_test_list else []

        raw_test_imports = args.get("test_imports", [])
        if isinstance(raw_test_imports, list):
            test_imports = [str(i) for i in raw_test_imports]
        else:
            test_imports = [str(raw_test_imports)] if raw_test_imports else []

        prompt_parts = [
            f"Solve the following MBPP Python problem:\n\n### Description:\n{task_description}"
        ]

        if test_imports:
            prompt_parts.append("### Test Imports:\n" + "\n".join(test_imports))

        if test_list:
            prompt_parts.append("### Test Cases:\n" + "\n".join(test_list))

        prompt_parts.append(
            "Write the solution, test it using `run_tests`, then submit via `final_answer`."
        )

        prompt_text = "\n\n".join(prompt_parts)

        result = {
            "description": "Solve MBPP Task Prompt",
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompt_text},
                }
            ],
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
            print("skipped an unparsable line", file=sys.stderr, flush=True)
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
