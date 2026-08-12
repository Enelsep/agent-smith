from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from agent_swebench.docker import DockerManager


def test_docker_manager_context_manager() -> None:
    with (
        patch("subprocess.run") as mock_run,
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_run.return_value = MagicMock(stdout="true", returncode=0)
        mock_popen.return_value = MagicMock(stdin=MagicMock(), returncode=None)

        with DockerManager("test-image:latest", "test-container") as mgr:
            assert mgr.container_id == "test-container"

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
        patch("subprocess.Popen") as mock_popen,
        patch.object(mgr, "bootstrap_dependencies") as mock_bootstrap,
    ):
        mock_run.return_value = MagicMock(stdout="true", returncode=0)
        mock_popen.return_value = MagicMock(stdin=MagicMock(), returncode=None)

        mgr.start()

        assert mgr.container_id == "test-container"
        mock_bootstrap.assert_called_once()


def test_what_the_container_needs_is_mounted_read_only_at_run_time() -> None:
    # Read-only because nothing in there is the container's to change, and a
    # mount rather than a transfer because a transfer restores the host's UID
    # inside the container, which a rootless daemon refuses.
    mgr = DockerManager(
        "test-image:latest",
        "test-container",
        mounts=[(Path("/host/server.py"), "/server.py")],
    )

    with (
        patch("subprocess.run") as mock_run,
        patch("subprocess.Popen") as mock_popen,
        patch.object(DockerManager, "bootstrap_dependencies"),
        patch.object(DockerManager, "cut_network"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="true", stderr="")
        mock_popen.return_value = MagicMock(stdin=MagicMock(), returncode=None)
        mgr.start()

    launched = mock_popen.call_args.args[0]
    assert "-v" in launched
    assert launched[launched.index("-v") + 1] == "/host/server.py:/server.py:ro"
    # The image and its command stay last, after every flag. `cat` because the
    # container is meant to die when our end of its stdin closes.
    assert launched[-2:] == ["test-image:latest", "cat"]
    assert "-i" in launched and "--rm" in launched


def test_locate_testbed_prefers_the_variable_the_image_sets() -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec:
        mock_exec.return_value = (0, "/srv/repo\n", "")

        assert mgr.locate_testbed() == "/srv/repo"


def test_locate_testbed_only_falls_back_to_a_directory_that_is_there() -> None:
    # The path is used as `docker exec -w`, and -w on a missing directory does
    # not warn -- it fails the exec, and with it every tool call.
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec:
        mock_exec.side_effect = [
            (0, "", ""),  # echo $TESTBED_PATH -> empty
            (1, "", ""),  # printenv TESTBED_PATH -> unset
            (1, "", ""),  # test -d /testbed -> absent
            (0, "/usr/src/app\n", ""),  # pwd
        ]

        assert mgr.locate_testbed() == "/usr/src/app"


def test_the_container_is_detached_from_every_network_it_is_on() -> None:
    # The sandbox denies the worker a socket; `run_command` runs a shell in the
    # container, where that guard does not reach. Measured on a real image, a
    # plain `docker run` leaves a container that reaches pypi.org.
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "abc123"

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout="bridge extra \n", returncode=0),
            MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=0, stderr=""),
        ]

        mgr.cut_network()

    disconnects = [
        call.args[0] for call in mock_run.call_args_list if "network" in call.args[0]
    ]
    assert disconnects == [
        ["docker", "network", "disconnect", "bridge", "abc123"],
        ["docker", "network", "disconnect", "extra", "abc123"],
    ]


def test_a_network_that_will_not_detach_does_not_kill_the_task() -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "abc123"

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout="bridge \n", returncode=0),
            MagicMock(returncode=1, stderr="no such network"),
        ]

        mgr.cut_network()


def test_the_route_out_is_cut_after_the_bootstrap_that_needs_it() -> None:
    # Order matters: installing ruff, jedi and ripgrep is the one startup step
    # that needs a network, and it runs before the agent has any say.
    mgr = DockerManager("test-image:latest", "test-container")
    order: list[str] = []

    with (
        patch("subprocess.run") as mock_run,
        patch("subprocess.Popen") as mock_popen,
        patch.object(
            mgr, "bootstrap_dependencies", side_effect=lambda: order.append("bootstrap")
        ),
        patch.object(mgr, "cut_network", side_effect=lambda: order.append("cut")),
    ):
        mock_run.return_value = MagicMock(stdout="true", returncode=0)
        mock_popen.return_value = MagicMock(stdin=MagicMock(), returncode=None)
        mgr.start()

    assert order == ["bootstrap", "cut"]
