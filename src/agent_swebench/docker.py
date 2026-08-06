from __future__ import annotations

import atexit
import contextlib
import logging
import subprocess
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
        """Cleans up orphan containers remaining active from a previous crash."""
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
    def start(self) -> None:
        """Pulls the image if necessary and starts the container."""
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
        except Exception:
            self.cleanup()
            raise

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

        return "/testbed"

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
