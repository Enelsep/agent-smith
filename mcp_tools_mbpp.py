from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from typing import Any


# ------------------------------------------------------------------------------
# Subprocess Test Execution Helper
# ------------------------------------------------------------------------------
def _run_tests(code: str, test_cases: str, timeout: float) -> dict[str, Any]:
    script_content = f"{code}\n\n# --- TEST CASES ---\n{test_cases}\n"

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
                            "test_cases": {
                                "type": "string",
                                "description": (
                                    "Assertion statements (e.g., `assert"
                                    " solution(1) == 2`)."
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
                        "required": ["code", "test_cases"],
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
        test_cases = str(args.get("test_cases", ""))
        timeout = float(args.get("timeout", 5.0))

        outcome = _run_tests(code, test_cases, timeout)
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
                            "name": "test_cases",
                            "description": "Test cases for the MBPP task",
                            "required": True,
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
        test_cases = str(args.get("test_cases", ""))

        prompt_text = (
            f"Solve the following MBPP Python problem:\n\n"
            f"### Description:\n{task_description}\n\n"
            f"### Test Cases:\n{test_cases}\n\n"
            f"Write the solution, test it using `run_tests`, then submit via `final_answer`."
        )

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
            continue

        if isinstance(request, dict):
            response = _handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
