"""
Migration v10 — Détection de staleness pour les notebooks de comparaison.

Contexte :
  automatisation_2/steps/s2_notebooks.py ne régénérait le notebook de comparaison
  d'un problème que s'il n'existait pas du tout (notebook_exists). Si de nouveaux
  algorithmes étaient ajoutés ensuite (enrichissement S3), le notebook restait figé
  sur l'ancien nombre d'algos — d'où des comparaisons montrant 3 algos sur 10.

Changement :
  - notebooks.n_algorithms : nombre d'algorithmes inclus au moment de la génération
    du notebook de comparaison. Permet de détecter qu'un notebook existant est
    devenu obsolète (n_algorithms actuel du problème > valeur stockée) sans avoir
    à ré-exécuter quoi que ce soit pour le savoir.
"""
from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger(__name__)

_ADD_COLUMNS = [
    ("notebooks", "n_algorithms",
     "ALTER TABLE notebooks ADD COLUMN n_algorithms INT NULL DEFAULT NULL AFTER algorithm_id"),
]


def run() -> Dict[str, int]:
    """Applique la migration v10. Idempotent."""
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        log.error("[MIGRATION_V10] MySQL indisponible")
        return {"ok": 0, "skip": 0, "error": 0}

    stats: Dict[str, int] = {"ok": 0, "skip": 0, "error": 0}
    try:
        with conn.cursor() as cur:
            for table, col, sql in _ADD_COLUMNS:
                cur.execute(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                    (table, col),
                )
                if cur.fetchone()[0] > 0:
                    log.debug("[MIGRATION_V10] %s.%s déjà existante — skip", table, col)
                    stats["skip"] += 1
                    continue
                try:
                    cur.execute(sql)
                    log.info("[MIGRATION_V10] ✓ %s.%s ajoutée", table, col)
                    stats["ok"] += 1
                except Exception as exc:
                    log.warning("[MIGRATION_V10] ✗ %s.%s : %s", table, col, exc)
                    stats["error"] += 1

        conn.commit()
    except Exception as exc:
        log.error("[MIGRATION_V10] Erreur globale : %s", exc)
        stats["error"] += 1
    finally:
        conn.close()

    log.info("[MIGRATION_V10] Terminé — ok=%d skip=%d error=%d",
             stats["ok"], stats["skip"], stats["error"])
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
