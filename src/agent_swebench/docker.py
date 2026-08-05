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
    """Gestionnaire de cycle de vie Docker pour SWE-bench.

    Garantit le nettoyage via :
    1. Context Manager (__enter__ / __exit__)
    2. Blocs try...finally internes
    3. Handler atexit
    4. Startup orphan sweep (balayage au démarrage)
    """

    def __init__(self, image_name: str, container_name: str | None = None) -> None:
        self.image_name = image_name
        self.container_name = container_name or f"swe-bench-{id(self)}"
        self.container_id: str | None = None

        # 3. Enregistrement atexit pour la sécurité globale du processus
        self._atexit_handler = self.cleanup
        atexit.register(self._atexit_handler)

    # ------------------------------------------------------------------
    # 4. Startup Orphan Sweep
    # ------------------------------------------------------------------
    @classmethod
    def sweep_orphans(cls) -> None:
        """Nettoie les conteneurs orphelins restés actifs après un crash précédent."""
        logger.info("Balayage des conteneurs orphelins Docker...")
        try:
            cmd = ["docker", "ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_ids = [
                cid.strip() for cid in result.stdout.strip().split() if cid.strip()
            ]

            for cid in container_ids:
                logger.warning(f"Suppression du conteneur orphelin : {cid}")
                subprocess.run(
                    ["docker", "rm", "-f", cid], capture_output=True, check=False
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Échec lors du balayage des orphelins : {e}")

    # ------------------------------------------------------------------
    # Lifecycle Methods
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Pull l'image si nécessaire et démarre le conteneur."""
        # 4. Sweep au démarrage
        self.sweep_orphans()

        # Suppression préventive si un conteneur avec le même nom existe déjà
        with contextlib.suppress(Exception):
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                check=False,
            )

        logger.info(
            f"Démarrage du conteneur {self.container_name} ({self.image_name})..."
        )

        # Pull de l'image avec fallback si locale
        try:
            subprocess.run(
                ["docker", "pull", self.image_name],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning(
                f"Échec du pull de {self.image_name} (tentative d'utilisation locale) : {exc}"
            )

        # Lancement du conteneur en tâche de fond (-d) avec le label de sécurité
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
        """Exécute une commande dans le conteneur."""
        target = self.container_id or self.container_name
        if not target:
            raise RuntimeError("Le conteneur n'est pas démarré.")

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
        """Localise le chemin ${TESTBED_PATH} dans le conteneur."""
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
        """Nettoie le conteneur de manière sûre (suppression forcée)."""
        target = self.container_id or self.container_name
        if target:
            logger.info(f"Nettoyage du conteneur : {target}")
            try:
                subprocess.run(
                    ["docker", "rm", "-f", target],
                    capture_output=True,
                    check=False,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Erreur lors de la suppression du conteneur {target} : {e}"
                )
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
        # 2. Nettoyage garanti via le bloc exit
        self.cleanup()
