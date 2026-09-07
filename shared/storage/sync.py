"""
shared.storage.sync — Synchronisation MySQL ↔ MinIO.

Détecte et répare les incohérences :
  - Références MySQL pointant vers des clés MinIO inexistantes → repair upload
  - Objets MinIO sans référence MySQL → orphelins signalés
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class SyncReport:
    bucket: str = ""
    mysql_refs: int = 0
    minio_objects: int = 0
    missing_in_minio: List[Dict] = field(default_factory=list)
    orphans_in_minio: List[str] = field(default_factory=list)
    repaired: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (
            f"[{self.bucket}] MySQL={self.mysql_refs} MinIO={self.minio_objects} "
            f"manquants={len(self.missing_in_minio)} "
            f"orphelins={len(self.orphans_in_minio)} "
            f"réparés={self.repaired}"
        )


def sync_notebooks(repair: bool = False,
                   local_fallback_dir: Optional[Path] = None) -> SyncReport:
    """Synchronise notebooks MySQL ↔ bucket MinIO notebooks."""
    from shared.db.connection import get_connection
    from shared.storage.client import get_storage, BUCKET_NOTEBOOKS

    report = SyncReport(bucket="notebooks")
    store = get_storage()

    conn = get_connection()
    if not conn:
        log.error("[SYNC] MySQL indisponible")
        return report

    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT notebook_key, minio_key, executed_minio_key "
                "FROM notebooks WHERE minio_key IS NOT NULL"
            )
            rows = cur.fetchall() or []
    except Exception as exc:
        log.warning("[SYNC] Lecture notebooks erreur : %s", exc)
        rows = []
    finally:
        conn.close()

    report.mysql_refs = len(rows)
    minio_keys = set(store.list_objects(BUCKET_NOTEBOOKS))
    report.minio_objects = len(minio_keys)
    mysql_keys: set = set()

    for row in rows:
        for field_name in ("minio_key", "executed_minio_key"):
            key = row.get(field_name)
            if not key:
                continue
            mysql_keys.add(key)
            if key not in minio_keys:
                report.missing_in_minio.append({
                    "notebook_key": row["notebook_key"],
                    "minio_key": key,
                })
                if repair and local_fallback_dir:
                    local = local_fallback_dir / f"{row['notebook_key']}.ipynb"
                    if local.exists():
                        res = store.upload_file(BUCKET_NOTEBOOKS, key, local)
                        if res.success:
                            report.repaired += 1
                            log.info("[SYNC] Réparé: %s", key)
                        else:
                            report.errors += 1

    report.orphans_in_minio = [k for k in minio_keys if k not in mysql_keys]
    log.info("[SYNC] %s", report.summary())
    return report


def sync_figures(repair: bool = False,
                 local_fallback_dir: Optional[Path] = None) -> SyncReport:
    """Synchronise figures MySQL ↔ bucket MinIO figures."""
    from shared.db.connection import get_connection
    from shared.storage.client import get_storage, BUCKET_FIGURES

    report = SyncReport(bucket="figures")
    store = get_storage()

    conn = get_connection()
    if not conn:
        return report

    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("SELECT figure_key, minio_key FROM figures WHERE minio_key IS NOT NULL")
            rows = cur.fetchall() or []
    except Exception:
        rows = []
    finally:
        conn.close()

    report.mysql_refs = len(rows)
    minio_keys = set(store.list_objects(BUCKET_FIGURES))
    report.minio_objects = len(minio_keys)
    mysql_keys = {r["minio_key"] for r in rows if r.get("minio_key")}

    for row in rows:
        key = row.get("minio_key")
        if key and key not in minio_keys:
            report.missing_in_minio.append({"figure_key": row["figure_key"], "minio_key": key})
            if repair and local_fallback_dir:
                local = local_fallback_dir / Path(key).name
                if local.exists():
                    res = store.upload_file(BUCKET_FIGURES, key, local)
                    if res.success:
                        report.repaired += 1

    report.orphans_in_minio = [k for k in minio_keys if k not in mysql_keys]
    log.info("[SYNC] %s", report.summary())
    return report


def sync_reports(repair: bool = False,
                 local_fallback_dir: Optional[Path] = None) -> SyncReport:
    """Synchronise reports MySQL ↔ bucket MinIO reports."""
    from shared.db.connection import get_connection
    from shared.storage.client import get_storage, BUCKET_REPORTS

    report = SyncReport(bucket="reports")
    store = get_storage()

    conn = get_connection()
    if not conn:
        return report

    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT report_key, minio_key FROM reports "
                "WHERE minio_key IS NOT NULL AND compile_success=1"
            )
            rows = cur.fetchall() or []
    except Exception:
        rows = []
    finally:
        conn.close()

    report.mysql_refs = len(rows)
    minio_keys = set(store.list_objects(BUCKET_REPORTS))
    report.minio_objects = len(minio_keys)
    mysql_keys = {r["minio_key"] for r in rows if r.get("minio_key")}

    for row in rows:
        key = row.get("minio_key")
        if key and key not in minio_keys:
            report.missing_in_minio.append({"report_key": row["report_key"], "minio_key": key})
            if repair and local_fallback_dir:
                # Essayer de trouver le PDF local
                type_slug = row["report_key"].replace("report_", "")
                local = local_fallback_dir / type_slug / "main.pdf"
                if local.exists():
                    res = store.upload_file(BUCKET_REPORTS, key, local)
                    if res.success:
                        report.repaired += 1

    report.orphans_in_minio = [k for k in minio_keys if k not in mysql_keys]
    log.info("[SYNC] %s", report.summary())
    return report


def sync_datasets(repair: bool = False,
                  local_fallback_dir: Optional[Path] = None) -> SyncReport:
    """Synchronise datasets MySQL ↔ bucket MinIO datasets."""
    from shared.db.connection import get_connection
    from shared.storage.client import get_storage, BUCKET_DATASETS

    report = SyncReport(bucket="datasets")
    store = get_storage()

    conn = get_connection()
    if not conn:
        return report

    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT dataset_key, minio_key FROM datasets WHERE minio_key IS NOT NULL"
            )
            rows = cur.fetchall() or []
    except Exception:
        rows = []
    finally:
        conn.close()

    report.mysql_refs = len(rows)
    minio_keys = set(store.list_objects(BUCKET_DATASETS))
    report.minio_objects = len(minio_keys)
    mysql_keys = {r["minio_key"] for r in rows if r.get("minio_key")}

    for row in rows:
        key = row.get("minio_key")
        if key and key not in minio_keys:
            report.missing_in_minio.append({"dataset_key": row["dataset_key"], "minio_key": key})
            if repair and local_fallback_dir:
                local = local_fallback_dir / Path(key).name
                if local.exists():
                    res = store.upload_file(BUCKET_DATASETS, key, local)
                    if res.success:
                        report.repaired += 1

    report.orphans_in_minio = [k for k in minio_keys if k not in mysql_keys]
    log.info("[SYNC] %s", report.summary())
    return report


def full_sync(repair: bool = False) -> Dict[str, SyncReport]:
    """Lance la synchronisation complète sur tous les buckets."""
    log.info("[SYNC] Démarrage synchronisation complète MySQL ↔ MinIO (repair=%s)", repair)
    results = {
        "notebooks": sync_notebooks(repair=repair, local_fallback_dir=Path("data/notebooks")),
        "figures":   sync_figures(repair=repair,   local_fallback_dir=Path("data/figures")),
        "reports":   sync_reports(repair=repair,   local_fallback_dir=Path("data/reports")),
        "datasets":  sync_datasets(repair=repair,  local_fallback_dir=Path("data/datasets")),
    }
    total_missing  = sum(len(r.missing_in_minio)  for r in results.values())
    total_orphans  = sum(len(r.orphans_in_minio)  for r in results.values())
    total_repaired = sum(r.repaired               for r in results.values())
    log.info("[SYNC] Terminé — manquants=%d orphelins=%d réparés=%d",
             total_missing, total_orphans, total_repaired)
    return results
