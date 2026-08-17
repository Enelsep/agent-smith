"""
TOOL-9: run_command tool implementation.

Executes a shell command in a specified working directory, capturing stdout, stderr,
exit code, with timeout and output truncation support.
"""

import subprocess
from pathlib import Path

MAX_OUTPUT_CHARS = 10000


def run_command(
    command: str,
    workdir: str = ".",
    timeout: int = 120,
) -> str:
    """Executes a shell command in the specified directory.

    Args:
        command: Shell command string to execute.
        workdir: Working directory for execution (default: '.').
        timeout: Maximum execution timeout in seconds (default: 120). Long
            enough for the `pip install` and single-file `pytest` a model
            reaches for, short enough that a command that never returns costs
            a fraction of the run's wall-clock budget rather than most of it.

    Returns:
        Formatted string containing exit code, stdout, stderr, and truncation notice if needed.
    """
    root_path = Path(workdir).resolve()

    if not root_path.exists():
        return f"Error: Working directory '{workdir}' does not exist."
    if not root_path.is_dir():
        return f"Error: Working directory '{workdir}' is not a directory."

    if not command.strip():
        return "Error: Command cannot be empty."

    try:
        process = subprocess.run(
            command,
            shell=True,
            cwd=str(root_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        exit_code = process.returncode
        stdout = process.stdout
        stderr = process.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr or "" if isinstance(exc.stderr, str) else ""
        return (
            f"Error: Command timed out after {timeout} seconds.\n"
            f"Partial stdout:\n{stdout[:1000]}\n"
            f"Partial stderr:\n{stderr[:1000]}"
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return f"Error executing command '{command}': {exc}"

    # Combine output streams
    combined_output = ""
    if stdout:
        combined_output += f"--- STDOUT ---\n{stdout.strip()}\n"
    if stderr:
        combined_output += f"--- STDERR ---\n{stderr.strip()}\n"

    if not combined_output:
        combined_output = "(No output produced)"
    if len(combined_output) > MAX_OUTPUT_CHARS:
        combined_output = (
            combined_output[:MAX_OUTPUT_CHARS]
            + f"\n\n[Truncated: Output exceeded {MAX_OUTPUT_CHARS} characters.]"
        )

    return f"Exit Code: {exit_code}\n{combined_output}"
