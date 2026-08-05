"""
TOOL-1: read_file tool implementation.

Reads a window of lines from a file with line numbers (cat -n style).
Clamps input ranges and enforces window caps to prevent context window overflow.
"""

from pathlib import Path

DEFAULT_WINDOW_SIZE = 100
MAX_WINDOW_SIZE = 250


def read_file(
    filepath: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    """Reads lines from a file with line numbers formatted as 'N: content'.

    Args:
        filepath: Path to the file to read.
        start_line: 1-based start line number (default: 1).
        end_line: 1-based end line number (inclusive). If None, defaults to start_line + DEFAULT_WINDOW_SIZE - 1.

    Returns:
        Formatted string containing line numbers and line text, or an error message.
    """
    path = Path(filepath)

    if not path.exists():
        return f"Error: File '{filepath}' does not exist."
    if not path.is_file():
        return f"Error: Path '{filepath}' is not a file."

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading file '{filepath}': {exc}"

    lines = content.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        return f"File '{filepath}' is empty."

    # Clamp start_line to valid bounds
    start = max(1, start_line)
    if start > total_lines:
        return (
            f"Error: start_line ({start_line}) exceeds total lines "
            f"({total_lines}) in '{filepath}'."
        )

    # Determine end_line default if not provided
    if end_line is None:
        end = start + DEFAULT_WINDOW_SIZE - 1
    else:
        end = end_line

    # Clamp end_line logic
    end = max(end, start)

    # Enforce maximum window size to avoid giant responses
    if (end - start + 1) > MAX_WINDOW_SIZE:
        end = start + MAX_WINDOW_SIZE - 1

    # Clamp end_line to actual total lines
    end = min(end, total_lines)

    # Format lines as "N: content"
    formatted_lines = [
        f"{line_num}: {lines[line_num - 1]}" for line_num in range(start, end + 1)
    ]

    return "\n".join(formatted_lines)
