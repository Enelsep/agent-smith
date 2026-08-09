"""Writing a file whole, for when a replacement cannot be expressed.

`edit_file` needs an `old_str` that matches exactly once. A model that cannot
produce one — because the text repeats, because it never read it accurately, or
because it wants a file that does not exist yet — has no way forward with the
mandatory tools, and a measured run showed one spending its entire output budget
failing at that. This is the way out, with the same syntax check `edit_file`
runs so a whole-file write cannot quietly leave a broken module behind.
"""

import ast
from pathlib import Path

from agent_smith.sandbox.feedback import edit_broke_file


def write_file(filepath: str, content: str) -> str:
    """Creates or overwrites a file with `content`.

    Args:
        filepath: Path to the target file. Parent directories are created.
        content: The complete new contents of the file.

    Returns:
        A confirmation naming what changed, or an error message.
    """
    path = Path(filepath)

    if path.exists() and not path.is_file():
        return f"Error: Path '{filepath}' is not a file."

    # Reported so the model knows whether it replaced work or started fresh:
    # overwriting a file it meant to create is a mistake worth seeing at once.
    existed = path.is_file()

    problem = ""
    if path.suffix == ".py":
        try:
            ast.parse(content, filename=str(path))
        except SyntaxError as invalid:
            problem = "\n" + edit_broke_file(filepath, str(invalid))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing '{filepath}': {exc}"

    lines = len(content.splitlines())
    what = "Overwrote" if existed else "Created"
    return f"{what} '{filepath}' ({lines} lines).{problem}"
