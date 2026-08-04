"""
TOOL-4: search_code tool implementation.

Searches for regex patterns in source files using ripgrep if available,
with a pure Python fallback. Formats output as '/abs/path.py:<line> <content>'
and caps total match count.
"""

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

MAX_MATCHES_LIMIT = 200


def search_code(
    pattern: str,
    file_pattern: str | None = None,
    directory: str = ".",
) -> str:
    """Searches for a regex pattern in code files under a directory.

    Args:
        pattern: Regex pattern to search for in file contents.
        file_pattern: Optional glob pattern to restrict file matching (e.g. '*.py').
        directory: Root directory for the search (default: '.').

    Returns:
        Formatted string containing matched lines or an error/status message.
    """
    root_path = Path(directory).resolve()

    if not root_path.exists():
        return f"Error: Directory '{directory}' does not exist."
    if not root_path.is_dir():
        return f"Error: Path '{directory}' is not a directory."

    # Validate regex pattern
    try:
        compiled_regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: Invalid regular expression pattern '{pattern}': {exc}"

    matches: list[str] = []

    # 1. Try ripgrep if available
    rg_path = shutil.which("rg")
    if rg_path:
        cmd = [rg_path, "-n", "--no-heading", pattern, str(root_path)]
        if file_pattern:
            cmd.extend(["-g", file_pattern])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, check=False
            )
            if result.returncode in (0, 1):
                raw_lines = result.stdout.splitlines()
                for line in raw_lines:
                    parts = line.split(":", 2)
                    if len(parts) == 3:
                        abs_file, line_num, content = parts
                        matches.append(f"{abs_file}:{line_num} {content}")
                return _format_matches(matches, pattern, directory)
        except Exception:  # noqa: BLE001
            matches.clear()  # Fallback to pure python implementation on failure

    # 2. Pure Python fallback traversal
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Skip hidden folders and common cache directories
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d != "__pycache__"
        ]

        for filename in filenames:
            if file_pattern and not fnmatch.fnmatch(filename, file_pattern):
                continue

            file_path = Path(dirpath) / filename
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    if compiled_regex.search(line):
                        matches.append(f"{file_path.resolve()}:{line_idx} {line}")
                        if len(matches) >= MAX_MATCHES_LIMIT + 1:
                            break
            except Exception:  # noqa: BLE001, S112
                continue

            if len(matches) >= MAX_MATCHES_LIMIT + 1:
                break

    return _format_matches(matches, pattern, directory)


def _format_matches(matches: list[str], pattern: str, directory: str) -> str:
    """Formats matches list with capping and truncation message."""
    if not matches:
        return f"No matches found for pattern '{pattern}' in '{directory}'."

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
