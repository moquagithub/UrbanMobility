"""
Migration v6 — Colonnes de suivi qualité des squelettes algorithmes.

Ajoute dans la table `algorithms` :
- needs_skeleton_regen (TINYINT) : 1 si le squelette doit être régénéré
- skeleton_regen_reason (TEXT)   : raison détectée par le validateur de qualité
"""
from __future__ import annotations

import logging
from shared.db.connection import get_connection

log = logging.getLogger("shared.db.migration_v6")


def run_migration() -> None:
    conn = get_connection()
    if not conn:
        log.error("Base de données non disponible")
        return
    try:
        with conn.cursor() as cur:
            _add_skeleton_regen_columns(cur)
        conn.commit()
        log.info("Migration v6 terminée.")
    finally:
        conn.close()


def _add_skeleton_regen_columns(cur) -> None:
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE()
          AND TABLE_NAME='algorithms'
          AND COLUMN_NAME='needs_skeleton_regen'
    """)
    if cur.fetchone()[0] == 0:
        cur.execute("""
            ALTER TABLE algorithms
            ADD COLUMN needs_skeleton_regen  TINYINT(1) NOT NULL DEFAULT 0,
            ADD COLUMN skeleton_regen_reason TEXT NULL
        """)
        log.info("Colonnes needs_skeleton_regen + skeleton_regen_reason ajoutées à 'algorithms'.")
    else:
        log.info("Colonnes déjà présentes dans 'algorithms'.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
