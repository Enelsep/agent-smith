"""
TOOL-2: edit_file tool implementation.

Performs exact string replacements in files. Fails if old_str is not found
or occurs multiple times. Performs post-edit AST verification on Python files.
"""

import ast
import re
import shutil
import subprocess
from pathlib import Path

from agent_smith.sandbox.feedback import edit_broke_file

LINT_TIMEOUT_SECONDS = 10

# "path:12:5: F401 [*] `os` imported but unused" -> the rule code.
_RULE_CODE = re.compile(r"^[^:]*:\d+:\d+:\s+(?P<code>[A-Z]+\d+)\b")


def _lint(source: str, filepath: str) -> list[str] | None:
    """Ruff's complaints about `source`, or None when it could not be asked.

    Linted through stdin so nothing is written anywhere to check it. A missing
    or failing linter returns None rather than an empty list, so that "ruff
    could not run" is never mistaken for "the file is clean".
    """
    ruff = shutil.which("ruff")
    if ruff is None:
        return None
    try:
        completed = subprocess.run(
            [
                ruff,
                "check",
                "--output-format=concise",
                "--stdin-filename",
                filepath,
                "-",
            ],
            input=source,
            capture_output=True,
            text=True,
            timeout=LINT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return [
        line
        for line in completed.stdout.splitlines()
        if _RULE_CODE.match(line) is not None
    ]


def _introduced_violation(before: str, after: str, filepath: str) -> str | None:
    """A lint rule the edit broke that was not already broken.

    Ruff reports the whole file, so reporting its first complaint would blame
    the edit for whatever the file arrived with -- and telling a model it
    introduced something it did not is worse than saying nothing. Comparing by
    rule code rather than by line survives the line numbers shifting.
    """
    new = _lint(after, filepath)
    if not new:
        return None
    old = _lint(before, filepath)
    if old is None:
        return None

    seen = {m.group("code") for line in old if (m := _RULE_CODE.match(line))}
    fresh = [
        line
        for line in new
        if (m := _RULE_CODE.match(line)) and m.group("code") not in seen
    ]
    if not fresh:
        return None
    first, remaining = fresh[0], len(fresh) - 1
    return f"{first} (and {remaining} more)" if remaining > 0 else first


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

    # What the edit did to the file, checked before it is written so the model
    # is told in the same breath as being told the edit succeeded. Syntax
    # first: a file that does not parse cannot be meaningfully linted.
    problem = ""
    if path.suffix == ".py":
        try:
            ast.parse(new_content, filename=str(path))
        except SyntaxError as syntax_exc:
            problem = "\n" + edit_broke_file(filepath, str(syntax_exc))
        else:
            violation = _introduced_violation(content, new_content, filepath)
            if violation is not None:
                problem = "\n" + edit_broke_file(filepath, violation, syntax=False)

    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing changes to '{filepath}': {exc}"

    return f"Successfully updated '{filepath}'.{problem}"
