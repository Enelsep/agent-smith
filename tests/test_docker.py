from __future__ import annotations

from unittest.mock import MagicMock, patch

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

        # Le cleanup doit être exécuté à la sortie du bloc with
        assert mgr.container_id is None


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


def test_locate_testbed() -> None:
    mgr = DockerManager("test-image:latest", "test-container")
    mgr.container_id = "cid123"

    with patch.object(mgr, "exec") as mock_exec:
        mock_exec.return_value = (0, "/custom/testbed\n", "")
        path = mgr.locate_testbed()
        assert path == "/custom/testbed"
