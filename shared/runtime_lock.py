"""
Verrou d'instance unique basé sur fcntl.flock.

Empêche deux processus de surveillance/réparation de tourner en parallèle sur la
même base : deux `--watch` simultanés réparent les mêmes issues, s'écrasent
mutuellement les skeletons et doublent la consommation de quota LLM (incident
récurrent — le check `duplicate_watch` existe justement pour le détecter APRÈS
coup ; ce verrou l'empêche en amont).

flock est libéré automatiquement par le noyau à la mort du processus, y compris
sur SIGKILL : pas de verrou fantôme à nettoyer à la main après un crash.

Usage :
    from shared.runtime_lock import single_instance, LockBusy

    try:
        with single_instance("quality-watch"):
            ...
    except LockBusy as exc:
        print(exc)   # « déjà en cours (PID 1234) »
"""
from __future__ import annotations

import errno
import fcntl
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger("shared.runtime_lock")


class LockBusy(RuntimeError):
    """Une autre instance détient déjà le verrou."""


def lock_dir() -> Path:
    """Répertoire des verrous (surchargeable via MOBILITY_LOCK_DIR)."""
    d = Path(os.getenv("MOBILITY_LOCK_DIR", Path(tempfile.gettempdir()) / "mobility_automation"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path(name: str) -> Path:
    return lock_dir() / f"{name}.lock"


def holder_pid(name: str) -> Optional[int]:
    """PID du détenteur du verrou, ou None s'il est libre."""
    path = lock_path(name)
    if not path.exists():
        return None
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            content = path.read_text(encoding="utf-8").strip()
            return int(content) if content.isdigit() else -1
        # On a pu verrouiller : personne ne le détenait.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


@contextmanager
def single_instance(name: str) -> Iterator[Path]:
    """
    Acquiert le verrou `name` pour la durée du bloc.
    Lève LockBusy si une autre instance le détient déjà.
    """
    path = lock_path(name)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            existing = path.read_text(encoding="utf-8").strip() or "?"
            raise LockBusy(
                f"'{name}' est déjà en cours (PID {existing}). "
                f"Verrou : {path}. Arrêtez l'autre instance ou attendez sa fin."
            ) from exc

        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        log.info("[LOCK] '%s' acquis (PID %d, %s)", name, os.getpid(), path)
        yield path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
        log.debug("[LOCK] '%s' libéré", name)
