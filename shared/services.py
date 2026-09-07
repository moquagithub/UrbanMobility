"""shared.services — Démarrage automatique de MySQL et MinIO avant le pipeline."""
from __future__ import annotations

import logging
import subprocess
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

_MINIO_SCRIPT = Path.home() / "start_minio.sh"


# ── MySQL ─────────────────────────────────────────────────────────────────────

def _mysql_is_running() -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", "mysql"],
            timeout=5, capture_output=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def _start_mysql() -> bool:
    # Dernier recours — MySQL est normally toujours actif (enabled au boot).
    # Sans capture_output : le prompt sudo s'affiche dans le terminal si besoin.
    try:
        r = subprocess.run(
            ["sudo", "systemctl", "start", "mysql"],
            timeout=20,
        )
        if r.returncode == 0:
            time.sleep(2)
            return _mysql_is_running()
        return False
    except Exception:
        return False


# ── MinIO ─────────────────────────────────────────────────────────────────────

def _minio_is_running() -> bool:
    """Check HTTP — fiable peu importe qui a lancé MinIO."""
    try:
        urllib.request.urlopen("http://localhost:9000/minio/health/live", timeout=3)
        return True
    except Exception:
        return False


def _start_minio() -> bool:
    # 1. Service systemd utilisateur (configuré via setup_autostart.sh)
    try:
        r = subprocess.run(
            ["systemctl", "--user", "start", "minio"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            time.sleep(2)
            if _minio_is_running():
                return True
    except Exception:
        pass

    # 2. Fallback : script shell ~/start_minio.sh
    if _MINIO_SCRIPT.exists():
        try:
            subprocess.run(["bash", str(_MINIO_SCRIPT)], timeout=15, check=False)
            return _minio_is_running()
        except Exception:
            pass

    return False


# ── Point d'entrée public ─────────────────────────────────────────────────────

class ServiceUnavailableError(RuntimeError):
    """Levée quand un service requis ne peut pas démarrer."""


def ensure_services_running() -> None:
    """Vérifie et démarre MySQL + MinIO. Lève ServiceUnavailableError si l'un échoue."""
    errors: list[str] = []

    # MySQL
    if _mysql_is_running():
        log.info("[SERVICES] MySQL deja en cours")
    else:
        log.info("[SERVICES] MySQL arrete — tentative de demarrage...")
        if _start_mysql():
            log.info("[SERVICES] MySQL demarre avec succes")
        else:
            errors.append(
                "MySQL est arrete et n'a pas pu demarrer.\n"
                "  Lancez : sudo systemctl start mysql"
            )

    # MinIO
    if _minio_is_running():
        log.info("[SERVICES] MinIO deja en cours")
    else:
        log.info("[SERVICES] MinIO arrete — tentative de demarrage...")
        if _start_minio():
            log.info("[SERVICES] MinIO demarre avec succes")
        else:
            errors.append(
                "MinIO est arrete et n'a pas pu demarrer.\n"
                "  Si c'est la premiere fois : bash setup_autostart.sh\n"
                "  Sinon : systemctl --user start minio"
            )

    if errors:
        raise ServiceUnavailableError("\n".join(errors))
