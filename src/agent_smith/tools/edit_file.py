"""
TOOL-2: edit_file tool implementation.

Performs exact string replacements in files. Fails if old_str is not found
or occurs multiple times. Performs post-edit AST verification on Python files.
"""

import ast
from pathlib import Path


def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """Replaces old_str with new_str in filepath.

    Args:
        filepath: Path to the target file.
        old_str: The exact string segment to replace.
        new_str: The new string segment to insert.

    Returns:
        Status message indicating success or failure reasons.
    """
    path = Path(filepath)

    if not path.exists():
        return f"Error: File '{filepath}' does not exist."
    if not path.is_file():
        return f"Error: Path '{filepath}' is not a file."

    if not old_str:
        return "Error: old_str cannot be empty."

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Error reading file '{filepath}': {exc}"

    count = content.count(old_str)

    if count == 0:
        return (
            f"Error: Could not find exact match for old_str in '{filepath}'. "
            "No changes made."
        )
    if count > 1:
        return (
            f"Error: Found {count} occurrences of old_str in '{filepath}'. "
            "old_str must match exactly once to avoid ambiguous edits. No changes made."
        )

    # Perform single exact replacement
    new_content = content.replace(old_str, new_str, 1)

    # AST syntax check for Python files
    ast_warning = ""
    if path.suffix == ".py":
        try:
            ast.parse(new_content, filename=str(path))
        except SyntaxError as syntax_exc:
            ast_warning = (
                f"\nWARNING: Post-edit AST parsing failed (SyntaxError): {syntax_exc}"
            )

    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing changes to '{filepath}': {exc}"

    return f"Successfully updated '{filepath}'.{ast_warning}"
