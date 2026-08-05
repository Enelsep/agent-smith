"""
Unit tests for TOOL-6 find_references.
"""

import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.find_references import find_references


class TestFindReferencesTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # File 1 defining a function
        self.file1 = self.root / "provider.py"
        self.file1.write_text(
            "def compute_data(value):\n    return value * 2\n",
            encoding="utf-8",
        )

        # File 2 consuming the function
        self.file2 = self.root / "consumer.py"
        self.file2.write_text(
            "from provider import compute_data\n\n"
            "def run():\n"
            "    res = compute_data(10)\n"
            "    print(res)\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_find_references_success(self) -> None:
        result = find_references(
            name="compute_data",
            filepath=str(self.file1),
            line=1,
            directory=str(self.root),
        )
        self.assertIn("provider.py:1 def compute_data(value):", result)
        self.assertIn("consumer.py", result)

    def test_find_references_file_not_found(self) -> None:
        result = find_references(
            name="compute_data",
            filepath=str(self.root / "missing.py"),
            line=1,
        )
        self.assertIn("Error: File", result)
        self.assertIn("does not exist", result)

    def test_find_references_fallback(self) -> None:
        # Invalid line number forces fallback to regex search
        result = find_references(
            name="compute_data",
            filepath=str(self.file1),
            line=999,
            directory=str(self.root),
        )
        self.assertIn("provider.py:1 def compute_data(value):", result)
        self.assertIn("consumer.py:1 from provider import compute_data", result)


if __name__ == "__main__":
    unittest.main()
