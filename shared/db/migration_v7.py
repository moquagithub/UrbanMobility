"""
Migration v7 — Colonnes minio_key pour cohérence MySQL ↔ MinIO.

Ajoute dans chaque table de contenu une colonne pointant vers la clé MinIO :
  notebooks.minio_key          — notebook source (.ipynb)
  notebooks.executed_minio_key — notebook exécuté avec outputs
  figures.minio_key            — figure PNG
  reports.minio_key            — PDF final
  datasets.minio_key           — CSV du dataset

Migration NON DESTRUCTIVE : aucune colonne n'est supprimée.
"""
from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger(__name__)

_MIGRATIONS = [
    ("notebooks", "minio_key",
     "ALTER TABLE notebooks ADD COLUMN minio_key VARCHAR(512) NULL DEFAULT NULL AFTER notebook_path"),
    ("notebooks", "executed_minio_key",
     "ALTER TABLE notebooks ADD COLUMN executed_minio_key VARCHAR(512) NULL DEFAULT NULL AFTER executed_path"),
    ("figures", "minio_key",
     "ALTER TABLE figures ADD COLUMN minio_key VARCHAR(512) NULL DEFAULT NULL AFTER file_path"),
    ("reports", "minio_key",
     "ALTER TABLE reports ADD COLUMN minio_key VARCHAR(512) NULL DEFAULT NULL AFTER pdf_path"),
    ("datasets", "minio_key",
     "ALTER TABLE datasets ADD COLUMN minio_key VARCHAR(512) NULL DEFAULT NULL AFTER file_path"),
]


def run() -> Dict[str, int]:
    """Applique toutes les migrations v7. Idempotent (skip si colonne existe déjà)."""
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        log.error("[MIGRATION_V7] MySQL indisponible")
        return {"ok": 0, "skip": 0, "error": 0}

    stats: Dict[str, int] = {"ok": 0, "skip": 0, "error": 0}
    try:
        with conn.cursor() as cur:
            for table, col, sql in _MIGRATIONS:
                cur.execute(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                    (table, col),
                )
                if cur.fetchone()[0] > 0:
                    log.debug("[MIGRATION_V7] %s.%s déjà existante — skip", table, col)
                    stats["skip"] += 1
                    continue
                try:
                    cur.execute(sql)
                    log.info("[MIGRATION_V7] ✓ %s.%s ajoutée", table, col)
                    stats["ok"] += 1
                except Exception as exc:
                    log.warning("[MIGRATION_V7] ✗ %s.%s : %s", table, col, exc)
                    stats["error"] += 1
        conn.commit()
    finally:
        conn.close()

    log.info("[MIGRATION_V7] ok=%d skip=%d error=%d",
             stats["ok"], stats["skip"], stats["error"])
    return stats
