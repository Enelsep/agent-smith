from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from agent_swebench.docker import CONTAINER_LABEL, DockerManager


def test_sweep_orphans() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout="cid123\ncid456\n", returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]
        DockerManager.sweep_orphans()

        assert mock_run.call_count == 3
        first_call = mock_run.call_args_list[0][0][0]
        assert first_call == [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label={CONTAINER_LABEL}",
        ]


def test_docker_manager_context_manager() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="container_id_123", returncode=0)

        with DockerManager("test-image:latest", "test-container") as mgr:
            assert mgr.container_id == "container_id_123"

        # Cleanup must be executed when exiting the with block
        assert mgr.container_id is None

        # Verify docker rm -f was actually called
        rm_calls = [
            call[0][0]
            for call in mock_run.call_args_list
            if call[0][0][:3] == ["docker", "rm", "-f"]
        ]
        assert any(
            "container_id_123" in cmd or "test-container" in cmd for cmd in rm_calls
        )


def test_docker_manager_exec() -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="hello", stderr="")

        code, out, _err = mgr.exec("echo hello", workdir="/testbed", env={"FOO": "BAR"})

        assert code == 0
        assert out == "hello"
        cmd_args = mock_run.call_args[0][0]
        assert "-w" in cmd_args and "/testbed" in cmd_args
        assert "-e" in cmd_args and "FOO=BAR" in cmd_args


def test_locate_testbed_success() -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec:
        mock_exec.return_value = (0, "/custom/testbed\n", "")
        path = mgr.locate_testbed()
        assert path == "/custom/testbed"


def test_locate_testbed_fallback_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec, caplog.at_level("INFO"):
        mock_exec.return_value = (0, "", "")
        path = mgr.locate_testbed()
        assert path == "/testbed"
        assert "falling back to /testbed" in caplog.text


def test_start_rollback_on_docker_run_failure() -> None:
    mgr = DockerManager("test-image:latest", "test-container")

    with (
        patch("subprocess.run") as mock_run,
        patch.object(mgr, "cleanup") as mock_cleanup,
    ):
        mock_run.side_effect = [
            MagicMock(stdout="", returncode=0),  # sweep_orphans
            MagicMock(returncode=0),  # preventive removal
            MagicMock(returncode=0),  # docker pull
            RuntimeError("Docker run failed"),  # docker run exception
        ]

        with pytest.raises(RuntimeError, match="Docker run failed"):
            mgr.start()

        mock_cleanup.assert_called_once()


def test_exec_timeout_expired_bytes_and_str() -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    # Case 1: TimeoutExpired with bytes
    with patch("subprocess.run") as mock_run:
        exc = subprocess.TimeoutExpired(
            cmd="echo hi",
            timeout=5.0,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )
        mock_run.side_effect = exc

        code, out, err = mgr.exec("echo hi", timeout=5.0)
        assert code == -1
        assert out == "partial stdout"
        assert err == "partial stderr"

    # Case 2: TimeoutExpired with str / None
    with patch("subprocess.run") as mock_run:
        exc = subprocess.TimeoutExpired(
            cmd="echo hi",
            timeout=5.0,
            output="partial str stdout",
            stderr=None,
        )
        mock_run.side_effect = exc

        code, out, err = mgr.exec("echo hi", timeout=5.0)
        assert code == -1
        assert out == "partial str stdout"
        assert "Execution timed out after 5.0 seconds." in err


def test_atexit_handler_registration_and_cleanup() -> None:
    with (
        patch("atexit.register") as mock_register,
        patch("atexit.unregister") as mock_unregister,
    ):
        mgr = DockerManager("test-image:latest", "test-container")
        mock_register.assert_called_once_with(mgr._atexit_handler)

        mgr.cleanup()
        mock_unregister.assert_called_once_with(mgr._atexit_handler)


def test_bootstrap_dependencies_success() -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec:
        mock_exec.return_value = (0, "Successfully installed", "")
        mgr.bootstrap_dependencies()

        assert mock_exec.call_count == 2
        mock_exec.assert_has_calls(
            [
                call("pip install --quiet ruff jedi", timeout=120.0),
                call("pip install --quiet ripgrep==14.1.0", timeout=120.0),
            ]
        )


def test_bootstrap_dependencies_pip_failure_apt_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec, caplog.at_level("WARNING"):
        # 1. Pip ruff/jedi fails, 2. Pip ripgrep fails, 3. apt-get succeeds
        mock_exec.side_effect = [
            (1, "", "No matching distribution found for ruff"),
            (1, "", "No matching distribution found for ripgrep"),
            (0, "apt updated and installed", ""),
        ]

        mgr.bootstrap_dependencies()

        assert mock_exec.call_count == 3
        assert "Failed to install ruff/jedi via pip" in caplog.text
        assert "Failed to install ripgrep==14.1.0 via pip" in caplog.text


def test_bootstrap_dependencies_total_failure_tolerated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec, caplog.at_level("WARNING"):
        # Everything raises or fails
        mock_exec.side_effect = RuntimeError("Container lost")

        # Must not raise an exception
        mgr.bootstrap_dependencies()

        assert "Exception during container dependency bootstrap" in caplog.text


def test_start_triggers_bootstrap_dependencies() -> None:
    mgr = DockerManager("test-image:latest", "test-container")

    with (
        patch("subprocess.run") as mock_run,
        patch.object(mgr, "bootstrap_dependencies") as mock_bootstrap,
    ):
        mock_run.side_effect = [
            MagicMock(stdout="", returncode=0),  # sweep_orphans
            MagicMock(returncode=0),  # preventive removal
            MagicMock(returncode=0),  # docker pull
            MagicMock(stdout="cid123\n", returncode=0),  # docker run
        ]

        mgr.start()

        assert mgr.container_id == "cid123"
        mock_bootstrap.assert_called_once()
