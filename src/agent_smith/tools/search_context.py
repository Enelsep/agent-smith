"""Searching and reading the surrounding lines in one call.

`search_code` answers with the matching line alone, so locating anything costs
a search and then a read. On SWE-bench that is two turns, and every turn re-sends
the whole transcript against a cumulative input ceiling. This returns the window
instead, and leaves `search_code` untouched: its signature and output format are
fixed by the subject.
"""

from pathlib import Path

from agent_smith.tools.search_code import search_code

MAX_WINDOWS = 20
"""Windows, not matches. Each one costs `2 * context_lines + 1` lines of budget,
so the cap that matters is how many of them come back."""


def search_code_with_context(
    pattern: str,
    file_pattern: str | None = None,
    context_lines: int = 3,
    directory: str = ".",
) -> str:
    """Searches for a regex pattern and returns each match with its neighbours.

    Args:
        pattern: Regex pattern to search for in file contents.
        file_pattern: Optional glob pattern to restrict file matching (e.g. '*.py').
        context_lines: How many lines to show either side of a match (default: 3).
        directory: Root directory for the search (default: '.').

    Returns:
        Matched lines marked with '>' among their context, as
        '/abs/path.py:<line> <content>', or an error/status message.
    """
    if context_lines < 0:
        return "Error: context_lines cannot be negative."

    found = search_code(pattern, file_pattern, directory)
    if not found or found.startswith(("Error:", "No matches")):
        return found

    hits: dict[str, list[int]] = {}
    for line in found.splitlines():
        path, _, rest = line.partition(":")
        number, _, _ = rest.partition(" ")
        if number.isdigit():
            hits.setdefault(path, []).append(int(number))

    windows = 0
    rendered: list[str] = []
    for path, numbers in hits.items():
        try:
            lines = (
                Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            )
        except OSError:
            continue

        # Overlapping windows would repeat the same lines, which is exactly the
        # budget this tool exists to save. Merged into runs instead.
        for first, last in _runs(sorted(set(numbers)), context_lines, len(lines)):
            if windows >= MAX_WINDOWS:
                rendered.append(f"\n[Truncated: showing {MAX_WINDOWS} windows.]")
                return "\n".join(rendered)
            if rendered:
                rendered.append("")
            for line_number in range(first, last + 1):
                mark = ">" if line_number in numbers else " "
                rendered.append(f"{path}:{line_number}{mark} {lines[line_number - 1]}")
            windows += 1

    return "\n".join(rendered) if rendered else found


def _runs(numbers: list[int], context_lines: int, total: int) -> list[tuple[int, int]]:
    """Merge the windows around `numbers` into non-overlapping line ranges."""
    merged: list[tuple[int, int]] = []
    for number in numbers:
        first = max(1, number - context_lines)
        last = min(total, number + context_lines)
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged
