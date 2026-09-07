"""
shared.storage.workspace — Workspace temporaire pour exécution des notebooks.

Pattern : Download MinIO → traitement local → Upload MinIO → nettoyage auto.

Usage :
    with NotebookWorkspace(type_slug, notebook_key, minio_key) as ws:
        execute_notebook(ws.local_path)
        ws.set_executed(executed_nb_path)
    # upload automatique à la sortie du context manager
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from shared.storage.client import get_storage, BUCKET_NOTEBOOKS, BUCKET_FIGURES

log = logging.getLogger(__name__)


class NotebookWorkspace:
    """
    Espace de travail temporaire pour l'exécution d'un notebook.

    - Télécharge le notebook depuis MinIO dans un dossier temp
    - Expose local_path pour l'exécution
    - Re-uploade les résultats (notebook exécuté + figures) à la fermeture
    - Nettoie le dossier temp dans tous les cas
    """

    def __init__(self, type_slug: str, notebook_key: str, minio_key: str) -> None:
        self.type_slug    = type_slug
        self.notebook_key = notebook_key
        self.minio_key    = minio_key
        self._tmpdir: Optional[Path] = None
        self.local_path: Optional[Path] = None
        self.executed_minio_key: Optional[str] = None
        self._figure_keys: List[str] = []

    def __enter__(self) -> "NotebookWorkspace":
        store = get_storage()
        self._tmpdir = Path(tempfile.mkdtemp(prefix=f"nb_{self.notebook_key[:20]}_"))
        self.local_path = self._tmpdir / f"{self.notebook_key}.ipynb"
        try:
            store.download_notebook(self.minio_key, self.local_path)
            log.debug("[WORKSPACE] ↓ %s → %s", self.minio_key, self.local_path)
        except Exception as exc:
            log.warning("[WORKSPACE] Download échoué %s : %s", self.minio_key, exc)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._tmpdir and self._tmpdir.exists():
            try:
                shutil.rmtree(self._tmpdir)
            except Exception as exc:
                log.warning("[WORKSPACE] Nettoyage tmpdir échoué : %s", exc)
        return False

    def set_executed(self, executed_path: Path) -> Optional[str]:
        """Upload le notebook exécuté vers MinIO. Retourne la clé MinIO ou None."""
        if not executed_path or not Path(executed_path).exists():
            return None
        store = get_storage()
        key = f"{self.type_slug}/{self.notebook_key}_executed.ipynb"
        result = store.upload_file(BUCKET_NOTEBOOKS, key, Path(executed_path))
        if result.success:
            self.executed_minio_key = key
            log.debug("[WORKSPACE] ↑ executed: %s", key)
            return key
        log.warning("[WORKSPACE] Upload executed échoué : %s", result.error)
        return None

    def upload_figure(self, figure_path: Path) -> Optional[str]:
        """Upload une figure vers MinIO. Retourne la clé MinIO ou None."""
        figure_path = Path(figure_path)
        if not figure_path.exists():
            return None
        store = get_storage()
        key = f"{self.type_slug}/{figure_path.name}"
        result = store.upload_file(BUCKET_FIGURES, key, figure_path)
        if result.success:
            self._figure_keys.append(key)
            return key
        return None

    @property
    def uploaded_figure_keys(self) -> List[str]:
        return list(self._figure_keys)


@contextmanager
def temp_figures_dir(type_slug: str, prob_key: str) -> Iterator[Path]:
    """
    Context manager qui crée un dossier temp pour les figures,
    et uploade tout le contenu PNG vers MinIO à la sortie.

    Usage :
        with temp_figures_dir("traces_gps", "p1") as fig_dir:
            plt.savefig(fig_dir / "nb001_chart.png")
        # → figures/traces_gps/p1/nb001_chart.png dans MinIO
    """
    tmpdir = Path(tempfile.mkdtemp(prefix=f"figs_{type_slug}_{prob_key}_"))
    try:
        yield tmpdir
        store = get_storage()
        png_files = list(tmpdir.glob("*.png"))
        if png_files:
            items = [
                (BUCKET_FIGURES, f"{type_slug}/{prob_key}/{f.name}", f)
                for f in png_files
            ]
            results = store.batch_upload(items)
            ok = sum(1 for r in results if r.success)
            log.info("[WORKSPACE] Figures uploadées %d/%d — %s/%s",
                     ok, len(results), type_slug, prob_key)
    finally:
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)
