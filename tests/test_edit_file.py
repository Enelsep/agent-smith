"""
Unit tests for TOOL-2 edit_file.
"""

import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.edit_file import edit_file


class TestEditFileTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.py_file = Path(self.temp_dir.name) / "script.py"

        initial_code = (
            "def greet(name):\n"
            "    print(f'Hello, {name}')\n\n"
            "def main():\n"
            "    greet('World')\n"
        )
        self.py_file.write_text(initial_code, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_edit_file_success(self) -> None:
        result = edit_file(
            str(self.py_file),
            old_str="greet('World')",
            new_str="greet('Agent')",
        )
        self.assertIn("Successfully updated", result)
        self.assertNotIn("WARNING", result)

        updated_content = self.py_file.read_text(encoding="utf-8")
        self.assertIn("greet('Agent')", updated_content)

    def test_edit_file_zero_matches(self) -> None:
        result = edit_file(
            str(self.py_file),
            old_str="non_existent_function()",
            new_str="something()",
        )
        self.assertIn("Error: Could not find exact match", result)

    def test_edit_file_multiple_matches(self) -> None:
        result = edit_file(
            str(self.py_file),
            old_str="def",
            new_str="async def",
        )
        self.assertIn("Error: Found 2 occurrences", result)

    def test_edit_file_ast_breakage_warning(self) -> None:
        # Intentionally break Python syntax
        result = edit_file(
            str(self.py_file),
            old_str="def main():",
            new_str="def main(:",
        )
        self.assertIn("Successfully updated", result)
        self.assertIn("WARNING: Post-edit AST parsing failed", result)


if __name__ == "__main__":
    unittest.main()
