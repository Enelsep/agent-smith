"""
TOOL-5: search_function_or_class_definition_in_code tool implementation.

Walks Python AST to locate function, async function, and class definitions.
Immune to comments, strings, and docstrings.
"""

import ast
import os
from pathlib import Path

MAX_MATCHES_LIMIT = 200


def search_function_or_class_definition_in_code(
    name: str,
    directory: str = ".",
) -> str:
    """Searches for Python function, async function, or class definitions matching 'name'.

    Args:
        name: Name of the target function, async function, or class (exact or partial match).
        directory: Root directory to search in (default: '.').

    Returns:
        Formatted string of matches formatted as
        '/abs/path.py:<line> <the def or class line as written>',
        or an informational error message.
    """
    root_path = Path(directory).resolve()

    if not root_path.exists():
        return f"Error: Directory '{directory}' does not exist."
    if not root_path.is_dir():
        return f"Error: Path '{directory}' is not a directory."

    if not name:
        return "Error: Target name cannot be empty."

    matches: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Exclude hidden directories, virtual environments, and caches
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d != "__pycache__"
        ]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            file_path = Path(dirpath) / filename
            _parse_and_find_definitions(file_path, name, matches)

            if len(matches) >= MAX_MATCHES_LIMIT + 1:
                break

        if len(matches) >= MAX_MATCHES_LIMIT + 1:
            break

    if not matches:
        return (
            f"No function or class definition matching '{name}' found in '{directory}'."
        )

    total_found = len(matches)
    truncated = False

    if total_found > MAX_MATCHES_LIMIT:
        matches = matches[:MAX_MATCHES_LIMIT]
        truncated = True

    output = "\n".join(matches)
    if truncated:
        output += (
            f"\n\n[Truncated: Showing {MAX_MATCHES_LIMIT} of {total_found}+ matches.]"
        )

    return output


def _parse_and_find_definitions(
    file_path: Path,
    target_name: str,
    matches: list[str],
) -> None:
    """Parses a Python file AST and appends matched definitions to the matches list."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
    except (SyntaxError, OSError):
        return

    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and target_name in node.name
        ):
            # The line says `def`, `async def` or `class` itself, so a separate
            # kind field would only repeat it.
            written = _definition_line(content, node.lineno) or node.name
            matches.append(f"{file_path.resolve()}:{node.lineno} {written}")


def _definition_line(content: str, lineno: int) -> str:
    """The `def` or `class` line itself, as written.

    Reporting the name alone answers with the string that was searched for.
    The signature is what the caller needs next -- the parameters, the return
    annotation -- and `search_code` already quotes the line it matched, so the
    two tools read the same way.
    """
    lines = content.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()
    return ""
