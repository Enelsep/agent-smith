"""
Unit tests for TOOL-4 search_code.
"""

import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.search_code import MAX_MATCHES_LIMIT, search_code


class TestSearchCodeTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Create sample files
        (self.root / "module.py").write_text(
            "def calculate_total():\n    return 42\n", encoding="utf-8"
        )
        (self.root / "helper.py").write_text(
            "def helper():\n    total = calculate_total()\n", encoding="utf-8"
        )
        (self.root / "notes.txt").write_text(
            "calculate_total is documented here.\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_search_code_basic(self) -> None:
        result = search_code(pattern="calculate_total", directory=str(self.root))
        self.assertIn("module.py:1 def calculate_total():", result)
        self.assertIn("helper.py:2     total = calculate_total()", result)
        self.assertIn("notes.txt:1 calculate_total is documented here.", result)

    def test_search_code_with_file_pattern(self) -> None:
        result = search_code(
            pattern="calculate_total",
            file_pattern="*.py",
            directory=str(self.root),
        )
        self.assertIn("module.py", result)
        self.assertIn("helper.py", result)
        self.assertNotIn("notes.txt", result)

    def test_search_code_invalid_regex(self) -> None:
        result = search_code(pattern="[invalid_regex", directory=str(self.root))
        self.assertIn("Error: Invalid regular expression pattern", result)

    def test_search_code_truncation(self) -> None:
        large_file = self.root / "large.py"
        content = "\n".join(
            f"target_pattern_val = {i}" for i in range(MAX_MATCHES_LIMIT + 50)
        )
        large_file.write_text(content, encoding="utf-8")

        result = search_code(pattern="target_pattern_val", directory=str(self.root))
        self.assertIn(f"[Truncated: Showing {MAX_MATCHES_LIMIT}", result)


if __name__ == "__main__":
    unittest.main()


def test_overlapping_windows_are_merged_rather_than_repeated(tmp_path: Path) -> None:
    # Two matches three lines apart would otherwise print the lines between
    # them twice, which is the budget this tool is meant to save.
    from agent_smith.tools.search_context import search_code_with_context

    (tmp_path / "m.py").write_text(
        "\n".join(f"line{n}" if n not in (5, 8) else f"hit{n}" for n in range(1, 13)),
        encoding="utf-8",
    )

    found = search_code_with_context("hit", "*.py", 3, str(tmp_path))

    assert found.count("line6") == 1
    assert "5> hit5" in found
    assert "8> hit8" in found


def test_a_search_with_no_match_says_so_rather_than_reading_files(
    tmp_path: Path,
) -> None:
    from agent_smith.tools.search_context import search_code_with_context

    (tmp_path / "m.py").write_text("nothing here\n", encoding="utf-8")

    assert "No matches" in search_code_with_context("absent", "*.py", 2, str(tmp_path))
