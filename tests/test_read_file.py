"""
Unit tests for TOOL-1 read_file.
"""

import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.read_file import (
    DEFAULT_WINDOW_SIZE,
    MAX_WINDOW_SIZE,
    read_file,
)


class TestReadFileTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_file = Path(self.temp_dir.name) / "sample.txt"

        # Create a file with 300 lines for window testing
        content = "\n".join(f"Line content {i}" for i in range(1, 301))
        self.test_file.write_text(content, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_read_file_default_window(self) -> None:
        result = read_file(str(self.test_file))
        lines = result.splitlines()

        self.assertEqual(len(lines), DEFAULT_WINDOW_SIZE)
        self.assertTrue(lines[0].startswith("1: Line content 1"))
        self.assertTrue(
            lines[-1].startswith(
                f"{DEFAULT_WINDOW_SIZE}: Line content {DEFAULT_WINDOW_SIZE}"
            )
        )

    def test_read_file_specific_range(self) -> None:
        result = read_file(str(self.test_file), start_line=10, end_line=15)
        lines = result.splitlines()

        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[0], "10: Line content 10")
        self.assertEqual(lines[-1], "15: Line content 15")

    def test_read_file_clamping_max_window(self) -> None:
        # Request 400 lines (exceeds MAX_WINDOW_SIZE)
        result = read_file(str(self.test_file), start_line=1, end_line=400)
        lines = result.splitlines()

        self.assertEqual(len(lines), MAX_WINDOW_SIZE)

    def test_read_file_clamping_out_of_bounds(self) -> None:
        # Negative start line should clamp to 1
        result = read_file(str(self.test_file), start_line=-5, end_line=5)
        lines = result.splitlines()

        self.assertTrue(lines[0].startswith("1: "))

    def test_read_file_non_existent(self) -> None:
        result = read_file("non_existent_file.txt")
        self.assertIn("Error: File 'non_existent_file.txt' does not exist.", result)


if __name__ == "__main__":
    unittest.main()
