"""
Unit tests for TOOL-5 search_function_or_class_definition_in_code.
"""

import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.search_definition import (
    search_function_or_class_definition_in_code,
)


class TestSearchDefinitionTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Code with functions, async functions, classes, and misleading comments
        sample_code = (
            "# Comment mentioning my_target_func\n"
            '"""Docstring referencing class MyTargetClass"""\n\n'
            "def my_target_func(x, y):\n"
            "    return x + y\n\n"
            "class MyTargetClass:\n"
            "    def method(self):\n"
            "        pass\n\n"
            "async def async_target_func():\n"
            "    # my_target_func mentioned in inner comment\n"
            "    await my_target_func(1, 2)\n"
        )
        (self.root / "module.py").write_text(sample_code, encoding="utf-8")

        # Invalid Python file
        (self.root / "broken.py").write_text("def broken_syntax(:", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_search_definition_finds_func_and_class(self) -> None:
        result = search_function_or_class_definition_in_code(
            name="MyTargetClass",
            directory=str(self.root),
        )
        self.assertIn("module.py:7 class MyTargetClass", result)

    def test_search_definition_async_func(self) -> None:
        result = search_function_or_class_definition_in_code(
            name="async_target_func",
            directory=str(self.root),
        )
        self.assertIn("module.py:11 async_function async_target_func", result)

    def test_search_definition_immune_to_comments(self) -> None:
        result = search_function_or_class_definition_in_code(
            name="my_target_func",
            directory=str(self.root),
        )
        lines = result.splitlines()

        # Should match line 4 (def my_target_func), but NOT line 1 (comment) or line 14 (call)
        self.assertEqual(len(lines), 1)
        self.assertIn("module.py:4 function my_target_func", lines[0])

    def test_search_definition_not_found(self) -> None:
        result = search_function_or_class_definition_in_code(
            name="non_existent_symbol",
            directory=str(self.root),
        )
        self.assertIn("No function or class definition matching", result)


if __name__ == "__main__":
    unittest.main()
