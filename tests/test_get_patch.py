"""
Unit tests for TOOL-8 get_patch.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.get_patch import get_patch


class TestGetPatchTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Initialize a git repo and make initial commit
        subprocess.run(
            ["git", "init"], cwd=str(self.root), capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "TestUser"],
            cwd=str(self.root),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(self.root),
            capture_output=True,
            check=True,
        )

        self.tracked_file = self.root / "app.py"
        self.tracked_file.write_text("print('v1')\n", encoding="utf-8")

        subprocess.run(
            ["git", "add", "app.py"],
            cwd=str(self.root),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=str(self.root),
            capture_output=True,
            check=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_patch_modified_tracked_file(self) -> None:
        # Modify tracked file
        self.tracked_file.write_text("print('v2')\n", encoding="utf-8")

        # Create an untracked scratch file
        scratch_file = self.root / "reproduce.py"
        scratch_file.write_text("import app\n", encoding="utf-8")

        patch = get_patch(str(self.root))

        # Diff must contain app.py changes
        self.assertIn("--- a/app.py", patch)
        self.assertIn("+++ b/app.py", patch)
        self.assertIn("-print('v1')", patch)
        self.assertIn("+print('v2')", patch)

        # Diff must NOT contain untracked reproduce.py
        self.assertNotIn("reproduce.py", patch)

    def test_get_patch_empty_diff(self) -> None:
        patch = get_patch(str(self.root))
        self.assertIn("No changes detected in tracked files", patch)

    def test_get_patch_not_a_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as non_git_dir:
            patch = get_patch(non_git_dir)
            self.assertIn("is not inside a Git repository", patch)


if __name__ == "__main__":
    unittest.main()
