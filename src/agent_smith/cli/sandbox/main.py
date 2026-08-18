"""The `sandbox` command line entry point.

uv run sandbox
uv run sandbox sandbox_template.json
uv run sandbox --mcp-stdio "python mcp_tools_mbpp.py" sandbox_template.json
uv run sandbox --mcp-server http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from agent_smith.cli.sandbox.mcp_bridge import MCPBridge, MCPBridgeError
from agent_smith.cli.sandbox.repl import run_repl, run_script
from agent_smith.config import ConfigError
from agent_smith.config.loader import load_sandbox_config
from agent_smith.mcp.formatter import to_sandbox_manual
from agent_smith.models.contract import SandboxConfig
from agent_smith.sandbox.process import Sandbox

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agent_smith.mcp.protocol import MCPClientProtocol, MCPToolDefinition
    from agent_smith.sandbox.process import ToolHandler

BANNER = "Agent Smith sandbox."

USAGE_EXIT = 2
"""argparse's own code for a bad invocation; a bad config file is the same
kind of mistake, so it leaves by the same door."""

RUNTIME_EXIT = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The invocations the subject shows, and no others.

    The config file is positional and optional, and the two transports are
    mutually exclusive: a session speaks to one MCP server or to none.
    """
    parser = argparse.ArgumentParser(
        prog="sandbox",
        description="Run an interactive, restricted Python sandbox.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        type=Path,
        help="sandbox configuration JSON (default: the built-in limits)",
    )
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--mcp-stdio",
        default=None,
        metavar="COMMAND",
        help='command serving MCP over stdio, e.g. "python mcp_tools_mbpp.py"',
    )
    transport.add_argument(
        "--mcp-server",
        default=None,
        metavar="URL",
        help="URL of an MCP server speaking streamable HTTP",
    )
    return parser.parse_args(argv)


def load_config(path: Path | None) -> SandboxConfig:
    """The limits for this session: the named file, or the contract's defaults."""
    if path is None:
        return SandboxConfig()
    return load_sandbox_config(path)


def build_client(args: argparse.Namespace) -> MCPClientProtocol | None:
    """The MCP client the flags ask for, or None for a prompt without tools.

    Imported inside the function, the way `cli.mbpp.main` imports its provider:
    `mcp.client` pulls in the MCP transport stack, and a bare `uv run sandbox`
    has no use for it.
    """
    if args.mcp_stdio is None and args.mcp_server is None:
        return None

    from agent_smith.mcp.client import UnifiedMCPClient

    if args.mcp_stdio is not None:
        try:
            parts = shlex.split(args.mcp_stdio)
        except ValueError as unparsable:
            raise ConfigError(
                f"--mcp-stdio is not a valid command line: {unparsable}"
            ) from unparsable
        if not parts:
            raise ConfigError("--mcp-stdio needs a command to run")
        command, *arguments = parts
        return UnifiedMCPClient(command=command, args=arguments, env=dict(os.environ))

    return UnifiedMCPClient(url=args.mcp_server)


def describe(sandbox: Sandbox, tool_defs: Sequence[MCPToolDefinition]) -> str:
    """The few lines that say what this prompt will and will not do.

    Read off the built sandbox rather than the config, because `from_config`
    is where an unset allowlist becomes the default one; the numbers here are
    the ones the worker is actually enforcing.

    When a server is connected its manual follows, generated from that
    server's own schemas. It is the same text the agent's prompt is given, so
    what a person tries by hand here is documented exactly as the model sees
    it.
    """
    callables = ", ".join(["final_answer", *(tool.name for tool in tool_defs)])
    directories = ", ".join(sandbox.allowed_directories) or "nothing"
    lines = [
        BANNER,
        (
            f"limits: {sandbox.timeout:g}s per entry, "
            f"{sandbox.max_memory_mb} MB, "
            f"{len(sandbox.authorized_imports)} authorized imports, "
            f"file access under {directories}"
        ),
        f"available: {callables}",
        "Type `exit` or press Ctrl-D to leave.",
    ]
    if tool_defs:
        lines += ["", to_sandbox_manual(list(tool_defs))]
    return "\n".join(lines)


def _die(message: str, code: int) -> NoReturn:
    print(f"sandbox: {message}", file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    """Open the prompt, and make sure the worker is shut down when it closes."""
    args = parse_args()

    try:
        config = load_config(args.config)
        client = build_client(args)
    except ConfigError as unusable:
        _die(str(unusable), USAGE_EXIT)

    with contextlib.ExitStack() as session:
        tool_defs: list[MCPToolDefinition] = []
        handler: ToolHandler | None = None

        if client is not None:
            bridge = MCPBridge(client)
            try:
                bridge.start()
            except MCPBridgeError as unreachable:
                # Name what was asked for: a wrong URL or a server that is not
                # up yet are the two ordinary causes, and both are visible from
                # the endpoint alone.
                endpoint = args.mcp_server or args.mcp_stdio
                _die(f"{unreachable} ({endpoint})", RUNTIME_EXIT)
            session.callback(bridge.close)
            tool_defs, handler = bridge.tool_defs, bridge.call

        sandbox = session.enter_context(
            Sandbox.from_config(config, tool_defs=tool_defs, tool_handler=handler)
        )
        banner = describe(sandbox, tool_defs)
        if sys.stdin.isatty():
            run_repl(sandbox, banner=banner)
        else:
            run_script(sandbox, sys.stdin.read(), banner=banner)
