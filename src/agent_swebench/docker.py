from __future__ import annotations

import atexit
import contextlib
import logging
import subprocess
from pathlib import Path
from types import TracebackType

from typing_extensions import Self

logger = logging.getLogger(__name__)

CONTAINER_LABEL = "agent-smith-swe=true"


class DockerManager:
    """Docker lifecycle manager for SWE-bench.

    Guarantees cleanup via:
    1. Context Manager (__enter__ / __exit__)
    2. Internal try...finally blocks
    3. atexit handler
    4. Startup orphan sweep
    """

    def __init__(self, image_name: str, container_name: str | None = None) -> None:
        self.image_name = image_name
        self.container_name = container_name or f"swe-bench-{id(self)}"
        self.container_id: str | None = None

        # 3. Register atexit handler for global process safety
        self._atexit_handler = self.cleanup
        atexit.register(self._atexit_handler)

    # ------------------------------------------------------------------
    # 4. Startup Orphan Sweep
    # ------------------------------------------------------------------
    @classmethod
    def sweep_orphans(cls) -> None:
        """Cleans up orphan containers remaining active from a previous crash.

        Note: Assumes exclusive ownership of the CONTAINER_LABEL on this machine.
        Should only be used in single-run environments where no concurrent
        benchmark tasks share this label.
        """
        logger.info("Sweeping orphan Docker containers...")
        try:
            cmd = ["docker", "ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_ids = [
                cid.strip() for cid in result.stdout.strip().split() if cid.strip()
            ]

            for cid in container_ids:
                logger.warning(f"Removing orphan container: {cid}")
                subprocess.run(
                    ["docker", "rm", "-f", cid], capture_output=True, check=False
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed during orphan sweep: {e}")

    # ------------------------------------------------------------------
    # Lifecycle Methods
    # ------------------------------------------------------------------
    def bootstrap_dependencies(self) -> None:
        """Bootstraps container-side developer tools (ruff, jedi, ripgrep).

        Attempts to install tools once at container startup with strict timeouts.
        - Split pip calls ensure ruff/jedi succeed even if ripgrep fails.
        - ripgrep is pinned to 14.1.0 to leverage its prebuilt manylinux_2_17 wheels,
          preventing source compilation failures on older glibc environments (SWE-bench).
        """
        logger.info("Bootstrapping container dependencies (ruff, jedi, ripgrep)...")
        try:
            # 1. Install ruff and jedi (Fast & reliable via PyPI wheels)
            code_py, stdout_py, stderr_py = self.exec(
                "pip install --quiet ruff jedi", timeout=120.0
            )
            if code_py != 0:
                logger.warning(
                    "Failed to install ruff/jedi via pip "
                    f"(exit code {code_py}): {stderr_py.strip() or stdout_py.strip()}"
                )

            # 2. Install ripgrep pinned to 14.1.0
            # PIN REASON: 14.1.0 is the last release with broad manylinux_2_17 wheel support.
            # Newer versions require glibc >= 2.39 and fail back to Rust source compilation.
            code_rg, stdout_rg, stderr_rg = self.exec(
                "pip install --quiet ripgrep==14.1.0", timeout=120.0
            )

            if code_rg != 0:
                logger.warning(
                    "Failed to install ripgrep==14.1.0 via pip "
                    f"(exit code {code_rg}): {stderr_rg.strip() or stdout_rg.strip()}. "
                    "Attempting apt-get fallback..."
                )
                code_apt, _, stderr_apt = self.exec(
                    "apt-get update && apt-get install -y ripgrep", timeout=120.0
                )
                if code_apt != 0:
                    logger.warning(
                        f"apt-get fallback for ripgrep also failed: {stderr_apt.strip()}"
                    )

            if code_py == 0 and (code_rg == 0 or code_apt == 0):
                logger.info("Successfully bootstrapped container dependencies.")

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Exception during container dependency bootstrap: {exc}")

    def cut_network(self) -> None:
        """Detach the container from every network it is on.

        The sandbox denies the worker a socket, but `run_command` runs a shell
        inside this container, where that guard does not reach. A container with
        a route out is one an agent can fetch a published fix through, and the
        subject scores that zero. Nothing legitimate needs the route once the
        tools are installed: the repository is checked out and the tests run
        offline.

        Failure is logged, not raised. A container with no network to remove is
        already in the state this wants, and a task must not die on a cleanup
        step this late in startup.
        """
        if self.container_id is None:
            return
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}",
                self.container_id,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for network in inspected.stdout.split():
            detached = subprocess.run(
                ["docker", "network", "disconnect", network, self.container_id],
                capture_output=True,
                text=True,
                check=False,
            )
            if detached.returncode != 0:
                logger.warning(
                    f"Could not detach the container from {network}: "
                    f"{detached.stderr.strip()}"
                )
            else:
                logger.info(f"Detached the container from {network}.")

    def start(self) -> None:
        """Pulls the image if necessary, starts the container, and bootstraps tools."""
        # 4. Startup sweep
        self.sweep_orphans()

        # Preventive removal if a container with the same name already exists
        with contextlib.suppress(Exception):
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                check=False,
            )

        logger.info(f"Starting container {self.container_name} ({self.image_name})...")

        # Pull image with local fallback
        try:
            subprocess.run(
                ["docker", "pull", self.image_name],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning(
                f"Failed to pull {self.image_name} (attempting local fallback): {exc}"
            )

        # Launch container in background (-d) with security label
        run_cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_name,
            "--label",
            CONTAINER_LABEL,
            self.image_name,
            "tail",
            "-f",
            "/dev/null",
        ]
        try:
            res = subprocess.run(run_cmd, capture_output=True, text=True, check=True)
            self.container_id = res.stdout.strip()
            # Bootstrap first: installing the tools is the one step that needs
            # a route out, and it happens before the agent has any say.
            self.bootstrap_dependencies()
            self.cut_network()
        except Exception:
            self.cleanup()
            raise

    def copy_in(self, source: Path, destination: str) -> None:
        """Copy a file or directory from the host into the running container.

        A failure carries what docker said. `CalledProcessError` renders only
        the command and the exit status, and on a graded run the error field of
        the solution file is the whole account of what went wrong.
        """
        if self.container_id is None:
            raise RuntimeError("the container is not running")
        copied = subprocess.run(
            ["docker", "cp", str(source), f"{self.container_id}:{destination}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if copied.returncode != 0:
            raise RuntimeError(
                f"could not copy {source} into the container: "
                f"{copied.stderr.strip() or copied.stdout.strip() or 'no output'}"
            )

    def exec(
        self,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        """Executes a command inside the container."""
        target = self.container_id or self.container_name
        if not target:
            raise RuntimeError("Container is not running.")

        cmd = ["docker", "exec"]
        if workdir:
            cmd.extend(["-w", workdir])
        if env:
            for key, val in env.items():
                cmd.extend(["-e", f"{key}={val}"])
        cmd.extend([target, "bash", "-c", command])

        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout.decode()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode()
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or f"Execution timed out after {timeout} seconds.")
            )
            return -1, stdout, stderr

    def locate_testbed(self) -> str:
        """Locates ${TESTBED_PATH} path inside the container."""
        code, stdout, _ = self.exec("echo $TESTBED_PATH")
        path = stdout.strip()
        if code == 0 and path:
            return path

        code, stdout, _ = self.exec("printenv TESTBED_PATH")
        path = stdout.strip()
        if code == 0 and path:
            return path

        # The fallback is checked rather than assumed: callers use this path as
        # a working directory, and `docker exec -w` on a directory that is not
        # there kills the exec outright. A wrong guess should cost accuracy,
        # not the session.
        code, _, _ = self.exec("test -d /testbed")
        if code == 0:
            logger.info("TESTBED_PATH variable not found; falling back to /testbed")
            return "/testbed"

        code, stdout, _ = self.exec("pwd")
        working_directory = stdout.strip()
        logger.warning(
            "neither TESTBED_PATH nor /testbed found; using the image's own "
            f"working directory {working_directory or '/'}"
        )
        return working_directory or "/"

    def cleanup(self) -> None:
        """Safely cleans up the container (forced removal)."""
        target = self.container_id or self.container_name
        if target:
            logger.info(f"Cleaning up container: {target}")
            try:
                subprocess.run(
                    ["docker", "rm", "-f", target],
                    capture_output=True,
                    check=False,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error while removing container {target}: {e}")
            finally:
                self.container_id = None
                if hasattr(self, "_atexit_handler"):
                    with contextlib.suppress(Exception):
                        atexit.unregister(self._atexit_handler)

    # ------------------------------------------------------------------
    # 1. Context Manager Protocol
    # ------------------------------------------------------------------
    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # 2. Guaranteed cleanup via exit block
        self.cleanup()
