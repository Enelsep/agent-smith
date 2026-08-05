"""
TOOL-3: list_files tool implementation.

Lists files matching a glob pattern in a directory with result capping
and truncation notices.
"""

from pathlib import Path

MAX_FILES_LIMIT = 500


def list_files(
    directory: str = ".",
    pattern: str = "*",
) -> str:
    """Lists files matching a glob pattern in a given directory.

    Args:
        directory: Path to the root directory to list (default: ".").
        pattern: Glob pattern to filter files (default: "*").

    Returns:
        Formatted string listing matching relative file paths, or an error message.
    """
    root_path = Path(directory)

    if not root_path.exists():
        return f"Error: Directory '{directory}' does not exist."
    if not root_path.is_dir():
        return f"Error: Path '{directory}' is not a directory."

    try:
        # Use glob if path separators or explicit wildcards are provided,
        # otherwise use rglob for recursive matching across subdirectories.
        if "**" in pattern or "/" in pattern or "\\" in pattern:
            matched_paths = list(root_path.glob(pattern))
        else:
            matched_paths = list(root_path.rglob(pattern))
    except OSError as exc:
        return f"Error matching pattern '{pattern}' in '{directory}': {exc}"

    # Filter to regular files and format as relative posix paths
    files = [
        path.relative_to(root_path).as_posix()
        for path in matched_paths
        if path.is_file()
    ]
    files.sort()

    if not files:
        return f"No files matched pattern '{pattern}' in directory '{directory}'."

    total_found = len(files)
    truncated = False

    if total_found > MAX_FILES_LIMIT:
        files = files[:MAX_FILES_LIMIT]
        truncated = True

    output = "\n".join(files)
    if truncated:
        output += f"\n\n[Truncated: Showing {MAX_FILES_LIMIT} of {total_found} matched files.]"

    return output
