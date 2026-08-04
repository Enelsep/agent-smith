"""
Unit tests for TOOL-7 run_tests.
"""

import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.run_tests import _parse_test_output, run_tests


class TestRunTestsTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_pytest_output(self) -> None:
        pytest_raw = (
            "================ FAILURES ================\n"
            "________________ test_sub ________________\n"
            "def test_sub(): assert sub(2, 1) == 0\n"
            "FAILED tests/test_math.py::test_sub\n"
            "FAILED tests/test_math.py::test_div\n"
            "===== 3 passed, 2 failed in 0.05s =====\n"
        )
        passed, failed, failing = _parse_test_output(pytest_raw)
        self.assertEqual(passed, 3)
        self.assertEqual(failed, 2)
        self.assertIn("tests/test_math.py::test_sub", failing)
        self.assertIn("tests/test_math.py::test_div", failing)

    def test_parse_unittest_output(self) -> None:
        unittest_raw = (
            "FAIL: test_add (test_calc.TestCalc)\n"
            "Traceback...\n"
            "Ran 5 tests in 0.01s\n"
            "FAILED (failures=1)\n"
        )
        passed, failed, failing = _parse_test_output(unittest_raw)
        self.assertEqual(passed, 4)
        self.assertEqual(failed, 1)
        self.assertIn("test_add", failing[0])

    def test_run_tests_execution_success(self) -> None:
        # Create a mock Python script that simulates a passing test
        script_path = self.root / "mock_test.py"
        script_path.write_text("print('1 passed in 0.01s')", encoding="utf-8")

        result = run_tests(
            eval_script="python mock_test.py",
            directory=str(self.root),
        )
        self.assertIn("Test Run Status: PASSED", result)
        self.assertIn("1 passed, 0 failed", result)

    def test_run_tests_execution_failure(self) -> None:
        # Create a mock script that outputs a failed test and non-zero exit code
        script_path = self.root / "mock_fail.py"
        script_path.write_text(
            "import sys\n"
            "print('FAILED test_foo.py::test_bar')\n"
            "print('1 passed, 1 failed')\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )

        result = run_tests(
            eval_script="python mock_fail.py",
            directory=str(self.root),
        )
        self.assertIn("Test Run Status: FAILED", result)
        self.assertIn("1 passed, 1 failed", result)
        self.assertIn("test_foo.py::test_bar", result)


if __name__ == "__main__":
    unittest.main()
