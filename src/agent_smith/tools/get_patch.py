"""
TOOL-8: get_patch tool implementation.

Generates git diff patch restricted to tracked files, ignoring untracked/scratch files.
Scratch/reproduction scripts should be placed in /tmp/agent to avoid repo pollution.
"""

import subprocess
from pathlib import Path


def get_patch(directory: str = ".") -> str:
    """Generates a git patch (diff) of changes to tracked files in the workspace.

    Untracked files (e.g., scratch scripts or reproduction code) are ignored.
    Scratch files should ideally be placed in '/tmp/agent'.

    Args:
        directory: Root directory of the git repository (default: '.').

    Returns:
        Unified diff string representing all modifications to tracked files,
        or an informational message if no changes exist or if git fails.
    """
    root_path = Path(directory).resolve()

    if not root_path.exists():
        return f"Error: Directory '{directory}' does not exist."
    if not root_path.is_dir():
        return f"Error: Path '{directory}' is not a directory."

    # Check if directory is inside a git repository
    try:
        check_repo = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if check_repo.returncode != 0:
            return f"Error: Directory '{directory}' is not inside a Git repository."
    except (subprocess.SubprocessError, OSError) as exc:
        return f"Error checking Git repository status: {exc}"

    # Generate patch using `git diff HEAD` to capture staged and unstaged tracked changes
    try:
        diff_proc = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        # Fall back to `git diff` if HEAD is not valid (e.g., initial repo with no commits yet)
        if diff_proc.returncode != 0:
            diff_proc = subprocess.run(
                ["git", "diff"],
                cwd=str(root_path),
                capture_output=True,
                text=True,
                check=False,
            )

        patch_output = diff_proc.stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return f"Error executing git diff: {exc}"

    if not patch_output:
        return "No changes detected in tracked files (patch is empty)."

    return patch_output
