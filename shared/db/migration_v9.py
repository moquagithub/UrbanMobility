"""
Migration v9 — Architecture par problème pour notebooks, figures et rapports.

Changements :
  - notebooks.algorithm_id : rendu nullable (1 notebook couvre TOUS les algos du problème)
  - figures.problem_id     : nouvelle colonne INT NULL (figure liée à un problème)
  - reports.problem_id     : nouvelle colonne INT NULL (rapport par problème)

Après cette migration, le pipeline devient :
  • 1 notebook de comparaison par problème  (clé : {type_id}_{prob_key}_comparison)
  • 1 figure de comparaison par problème    (clé : compare_{type_id}_{prob_key})
  • 1 rapport PDF par problème              (clé : report_{type_id}_{prob_key})
"""
from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger(__name__)

# Migrations ADD COLUMN — idempotentes via INFORMATION_SCHEMA
_ADD_COLUMNS = [
    ("figures", "problem_id",
     "ALTER TABLE figures ADD COLUMN problem_id INT NULL DEFAULT NULL AFTER data_type_id"),
    ("reports", "problem_id",
     "ALTER TABLE reports ADD COLUMN problem_id INT NULL DEFAULT NULL AFTER data_type_id"),
]

# Migration MODIFY — rend algorithm_id nullable dans notebooks
_MODIFY_COLUMNS = [
    ("notebooks", "algorithm_id",
     "ALTER TABLE notebooks MODIFY COLUMN algorithm_id INT NULL DEFAULT NULL"),
]


def run() -> Dict[str, int]:
    """Applique toutes les migrations v9. Idempotent."""
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        log.error("[MIGRATION_V9] MySQL indisponible")
        return {"ok": 0, "skip": 0, "error": 0}

    stats: Dict[str, int] = {"ok": 0, "skip": 0, "error": 0}
    try:
        with conn.cursor() as cur:
            # ADD COLUMN (idempotent via INFORMATION_SCHEMA)
            for table, col, sql in _ADD_COLUMNS:
                cur.execute(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                    (table, col),
                )
                if cur.fetchone()[0] > 0:
                    log.debug("[MIGRATION_V9] %s.%s déjà existante — skip", table, col)
                    stats["skip"] += 1
                    continue
                try:
                    cur.execute(sql)
                    log.info("[MIGRATION_V9] ✓ %s.%s ajoutée", table, col)
                    stats["ok"] += 1
                except Exception as exc:
                    log.warning("[MIGRATION_V9] ✗ %s.%s : %s", table, col, exc)
                    stats["error"] += 1

            # MODIFY COLUMN — vérifier si IS_NULLABLE = YES avant d'appliquer
            for table, col, sql in _MODIFY_COLUMNS:
                cur.execute(
                    "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                    (table, col),
                )
                row = cur.fetchone()
                if not row:
                    log.debug("[MIGRATION_V9] %s.%s introuvable — skip", table, col)
                    stats["skip"] += 1
                    continue
                if row[0] == "YES":
                    log.debug("[MIGRATION_V9] %s.%s déjà nullable — skip", table, col)
                    stats["skip"] += 1
                    continue
                try:
                    cur.execute(sql)
                    log.info("[MIGRATION_V9] ✓ %s.%s rendu nullable", table, col)
                    stats["ok"] += 1
                except Exception as exc:
                    log.warning("[MIGRATION_V9] ✗ %s.%s MODIFY : %s", table, col, exc)
                    stats["error"] += 1

        conn.commit()
    except Exception as exc:
        log.error("[MIGRATION_V9] Erreur globale : %s", exc)
        stats["error"] += 1
    finally:
        conn.close()

    log.info("[MIGRATION_V9] Terminé — ok=%d skip=%d error=%d",
             stats["ok"], stats["skip"], stats["error"])
    return stats
