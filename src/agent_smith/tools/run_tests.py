"""
TOOL-7: run_tests tool implementation.

Runs an evaluation script or test runner, captures output, and parses it into
structured test metrics (N passed, M failed) along with failing test names.
"""

import re
import subprocess
from pathlib import Path

PASSED_STATUS = "Test Run Status: PASSED"
FAILED_STATUS = "Test Run Status: FAILED"
"""The verdict line, named so a caller can read it without quoting a literal
that this module is free to reword. `cli.swebench` refuses a submission on it."""


def run_tests(
    eval_script: str = "pytest",
    directory: str = ".",
    timeout: int = 60,
) -> str:
    """Executes a test evaluation script and parses the output into structured test metrics.

    Args:
        eval_script: Command or script path to run tests (default: 'pytest').
        directory: Root directory in which to run tests (default: '.').
        timeout: Maximum execution time in seconds (default: 60).

    Returns:
        Formatted summary string with passed/failed counts, failing test names,
        and raw failure output.
    """
    root_path = Path(directory).resolve()

    if not root_path.exists():
        return f"Error: Directory '{directory}' does not exist."
    if not root_path.is_dir():
        return f"Error: Path '{directory}' is not a directory."

    try:
        process = subprocess.run(
            eval_script,
            shell=True,
            cwd=str(root_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        raw_output = f"{process.stdout}\n{process.stderr}".strip()
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        return f"Error: Test execution timed out after {timeout} seconds."
    except (subprocess.SubprocessError, OSError) as exc:
        return f"Error executing test script '{eval_script}': {exc}"

    passed_count, failed_count, failing_names = _parse_test_output(raw_output)

    # Format structured output
    passed = exit_code == 0 and failed_count == 0
    lines = [
        f"{PASSED_STATUS if passed else FAILED_STATUS} (Exit code: {exit_code})",
        f"Summary: {passed_count} passed, {failed_count} failed",
    ]

    if failing_names:
        lines.append("\nFailing Test Names:")
        for name in failing_names:
            lines.append(f"  - {name}")

    if exit_code != 0 and raw_output:
        lines.append("\n--- Raw Output Snippet ---")
        # Include tail of raw output to keep context manageable
        output_lines = raw_output.splitlines()
        snippet = (
            "\n".join(output_lines[-40:]) if len(output_lines) > 40 else raw_output
        )
        lines.append(snippet)

    return "\n".join(lines)


def _parse_test_output(output: str) -> tuple[int, int, list[str]]:
    """Parses test stdout/stderr for passed/failed counts and failing test identifiers."""
    passed = 0
    failed = 0
    failing_names: list[str] = []

    # 1. Parse Pytest format
    # Summary line example: "5 passed, 2 failed, 1 error in 0.12s"
    pytest_passed = re.search(r"(\d+)\s+passed", output)
    pytest_failed = re.search(r"(\d+)\s+failed", output)
    pytest_errors = re.search(r"(\d+)\s+error", output)

    if pytest_passed or pytest_failed or pytest_errors:
        if pytest_passed:
            passed += int(pytest_passed.group(1))
        if pytest_failed:
            failed += int(pytest_failed.group(1))
        if pytest_errors:
            failed += int(pytest_errors.group(1))

        # Extract failed test names (e.g. "FAILED test_module.py::test_case")
        failing_names.extend(re.findall(r"^FAILED\s+([^\s]+)", output, re.MULTILINE))
        return passed, failed, failing_names

    # 2. Parse Unittest format
    # Example: "Ran 10 tests in 0.05s" / "FAILED (failures=2, errors=1)"
    ran_match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    unittest_fail_match = re.search(
        r"FAILED\s*\((?:failures=(\d+))?,?\s*(?:errors=(\d+))?\)", output
    )

    if ran_match:
        total_ran = int(ran_match.group(1))
        num_failures = (
            int(unittest_fail_match.group(1) or 0)
            if unittest_fail_match and unittest_fail_match.group(1)
            else 0
        )
        num_errors = (
            int(unittest_fail_match.group(2) or 0)
            if unittest_fail_match and unittest_fail_match.group(2)
            else 0
        )

        failed = num_failures + num_errors
        passed = max(0, total_ran - failed)

        # Extract failing test cases (e.g. "FAIL: test_feature (test_mod.TestClass)")
        failing_names.extend(
            re.findall(r"^(?:FAIL|ERROR):\s+([^\s]+)", output, re.MULTILINE)
        )
        return passed, failed, failing_names

    # 3. Generic fallback heuristics
    for line in output.splitlines():
        if re.search(r"\b(FAIL|FAILED|ERROR)\b", line, re.IGNORECASE):
            failing_names.append(line.strip())

    failed = len(failing_names)
    return passed, failed, failing_names
