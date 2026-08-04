"""
Unit tests for TOOL-9 run_command.
"""

import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.run_command import MAX_OUTPUT_CHARS, run_command


class TestRunCommandTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_command_success(self) -> None:
        result = run_command("echo 'Hello Agent Smith'", workdir=str(self.root))
        self.assertIn("Exit Code: 0", result)
        self.assertIn("Hello Agent Smith", result)

    def test_run_command_non_zero_exit_code(self) -> None:
        result = run_command(
            "python -c 'import sys; print(\"error_msg\", file=sys.stderr); sys.exit(42)'",
            workdir=str(self.root),
        )
        self.assertIn("Exit Code: 42", result)
        self.assertIn("--- STDERR ---", result)
        self.assertIn("error_msg", result)

    def test_run_command_invalid_workdir(self) -> None:
        result = run_command("ls", workdir="/invalid_directory_path_xyz")
        self.assertIn("Error: Working directory", result)
        self.assertIn("does not exist", result)

    def test_run_command_timeout(self) -> None:
        result = run_command(
            "python -c 'import time; time.sleep(5)'", workdir=str(self.root), timeout=1
        )
        self.assertIn("Error: Command timed out after 1 seconds", result)

    def test_run_command_truncation(self) -> None:
        long_str = "x" * (MAX_OUTPUT_CHARS + 500)
        result = run_command(
            f"python -c 'print(\"{long_str}\")'", workdir=str(self.root)
        )
        self.assertIn("[Truncated: Output exceeded", result)


if __name__ == "__main__":
    unittest.main()
