"""
Unit tests for TOOL-7 run_tests.
"""

import sys
import tempfile
import unittest
from pathlib import Path

from agent_smith.tools.run_tests import (
    PASSED_STATUS,
    _parse_test_output,
    run_tests,
)


class TestRunTestsTool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_a_bash_evaluation_script_runs(self) -> None:
        # Every SWE-bench task ships its evaluation as bash: `set -o pipefail`,
        # `source`, `conda activate`. Under /bin/sh -- dash on Debian and on
        # the task images -- the script dies on its second line, so the tool
        # reported FAILED for a repository that was fine. Measured on
        # django__django-11066: "/bin/sh: 2: set: Illegal option -o pipefail".
        script = "#!/bin/bash\nset -uxo pipefail\necho '1 passed'\n"

        result = run_tests(eval_script=script, directory=str(self.root))

        self.assertIn(PASSED_STATUS, result)
        self.assertNotIn("Illegal option", result)

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
            eval_script=f"{sys.executable} mock_test.py",
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
            eval_script=f"{sys.executable} mock_fail.py",
            directory=str(self.root),
        )
        self.assertIn("Test Run Status: FAILED", result)
        self.assertIn("1 passed, 1 failed", result)
        self.assertIn("test_foo.py::test_bar", result)


if __name__ == "__main__":
    unittest.main()


def test_a_script_that_ends_on_a_restore_does_not_pass_on_its_exit_code() -> None:
    # Measured on `sympy__sympy-14711`: the evaluation script's last command is
    # `git checkout <commit> <test file>`, which succeeds whatever the tests
    # did, so the script exits 0 with a failing suite. A model reading PASSED
    # there has no reason left to look for the bug.
    from agent_smith.tools.run_tests import _parse_test_output

    output = (
        ": '>>>>> Start Test Output'\n"
        "TypeError: A Vector must be supplied\n"
        "=========== tests finished: 3 passed, 1 exceptions, in 2.22 seconds ====\n"
        ": '>>>>> End Test Output'\n"
        "+ git checkout c6753448b sympy/physics/vector/tests/test_vector.py\n"
        "Updated 1 path from 0bd77345d\n"
    )

    passed, failed, _ = _parse_test_output(output)

    assert passed == 3
    assert failed == 1


def test_what_the_restore_step_prints_is_not_read_as_a_result() -> None:
    # Everything after the end marker is the script putting the checkout back.
    from agent_smith.tools.run_tests import test_region

    output = (
        ": '>>>>> Start Test Output'\n"
        "2 passed\n"
        ": '>>>>> End Test Output'\n"
        "FAILED to remove stale file\n"
    )

    assert "FAILED" not in test_region(output)


def test_a_test_that_raised_counts_even_under_a_word_we_do_not_know() -> None:
    from agent_smith.tools.run_tests import _parse_test_output

    output = "1 passed\nTraceback (most recent call last):\n  ValueError: no\n"

    _, failed, names = _parse_test_output(output)

    assert failed == 1
    assert names
