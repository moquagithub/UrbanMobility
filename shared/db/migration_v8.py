"""
Migration v8 — Colonnes minio_dir pour problems et algorithms.

Ajoute dans chaque table le préfixe Minio du répertoire associé :
  problems.minio_dir   — ex: "traces_gps/problems/p1/"
  algorithms.minio_dir — ex: "traces_gps/problems/p1/algorithms/alg1/"

Contrairement à minio_key (un fichier unique), minio_dir désigne un
répertoire sous lequel plusieurs fichiers sont stockés (metadata.json,
skeleton.py, explanation.txt pour les algos ; metadata.json,
test_results.json, dataset/ pour les problèmes).

Migration NON DESTRUCTIVE : aucune colonne n'est supprimée.
"""
from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger(__name__)

_MIGRATIONS = [
    ("problems", "minio_dir",
     "ALTER TABLE problems ADD COLUMN minio_dir VARCHAR(512) NULL DEFAULT NULL "
     "AFTER data_type_id"),
    ("algorithms", "minio_dir",
     "ALTER TABLE algorithms ADD COLUMN minio_dir VARCHAR(512) NULL DEFAULT NULL "
     "AFTER problem_id"),
]


def run() -> Dict[str, int]:
    """Applique toutes les migrations v8. Idempotent (skip si colonne existe déjà)."""
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        log.error("[MIGRATION_V8] MySQL indisponible")
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
                    log.debug("[MIGRATION_V8] %s.%s déjà existante — skip", table, col)
                    stats["skip"] += 1
                    continue
                try:
                    cur.execute(sql)
                    log.info("[MIGRATION_V8] ✓ %s.%s ajoutée", table, col)
                    stats["ok"] += 1
                except Exception as exc:
                    log.warning("[MIGRATION_V8] ✗ %s.%s : %s", table, col, exc)
                    stats["error"] += 1
        conn.commit()
    finally:
        conn.close()

    log.info("[MIGRATION_V8] ok=%d skip=%d error=%d",
             stats["ok"], stats["skip"], stats["error"])
    return stats
