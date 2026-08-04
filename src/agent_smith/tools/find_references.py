"""
TOOL-6: find_references tool implementation.

Uses Jedi to find precise symbol references, falling back to word-boundary regex
and AST searches across Python files so it never hard-fails.
"""

import os
import re
from pathlib import Path

MAX_REFERENCES_LIMIT = 200

try:
    import jedi

    HAS_JEDI = True
except ImportError:
    HAS_JEDI = False


def find_references(
    name: str,
    filepath: str,
    line: int,
    directory: str = ".",
) -> str:
    """Finds references to a symbol defined or used at (filepath, line).

    Args:
        name: The symbol name to find references for.
        filepath: Path to the file containing the symbol reference.
        line: 1-indexed line number where the symbol occurs.
        directory: Directory root for search scope (default: '.').

    Returns:
        Formatted string of references '/abs/path.py:<line> <content>',
        or an informational error message.
    """
    path = Path(filepath).resolve()
    root_dir = Path(directory).resolve()

    if not path.exists():
        return f"Error: File '{filepath}' does not exist."
    if not path.is_file():
        return f"Error: Path '{filepath}' is not a file."

    if not name:
        return "Error: Symbol name cannot be empty."

    matches: list[str] = []

    # 1. Attempt Jedi-based reference resolution
    if HAS_JEDI:
        try:
            matches = _find_with_jedi(name, path, line)
        except Exception:  # noqa: BLE001
            matches = []

    # 2. Fallback regex/AST search if Jedi fails or returns no matches
    if not matches:
        matches = _find_fallback(name, root_dir)

    if not matches:
        return f"No references found for symbol '{name}'."

    total_found = len(matches)
    truncated = False

    if total_found > MAX_REFERENCES_LIMIT:
        matches = matches[:MAX_REFERENCES_LIMIT]
        truncated = True

    output = "\n".join(matches)
    if truncated:
        output += f"\n\n[Truncated: Showing {MAX_REFERENCES_LIMIT} of {total_found}+ references.]"

    return output


def _find_with_jedi(name: str, path: Path, line: int) -> list[str]:
    """Resolves symbol references using Jedi."""
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    if line < 1 or line > len(lines):
        return []

    target_line = lines[line - 1]
    col = target_line.find(name)
    if col == -1:
        col = 0

    script = jedi.Script(code=content, path=path)
    references = script.get_references(line=line, column=col)

    results: list[str] = []
    for ref in references:
        ref_path = ref.module_path
        if not ref_path:
            continue

        ref_line_no = ref.line
        ref_content = ""
        try:
            ref_lines = (
                Path(ref_path)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            )
            if 1 <= ref_line_no <= len(ref_lines):
                ref_content = ref_lines[ref_line_no - 1]
        except Exception:  # noqa: BLE001, S110
            pass

        results.append(f"{Path(ref_path).resolve()}:{ref_line_no} {ref_content}")

    return results


def _find_fallback(name: str, root_dir: Path) -> list[str]:
    """Fallback search using word-boundary regex across all Python files."""
    results: list[str] = []
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d != "__pycache__"
        ]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            file_path = Path(dirpath) / filename
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                for line_idx, line_str in enumerate(content.splitlines(), start=1):
                    if pattern.search(line_str):
                        results.append(f"{file_path.resolve()}:{line_idx} {line_str}")
                        if len(results) >= MAX_REFERENCES_LIMIT + 1:
                            break
            except Exception:  # noqa: BLE001, S112
                continue

            if len(results) >= MAX_REFERENCES_LIMIT + 1:
                break

    return results
