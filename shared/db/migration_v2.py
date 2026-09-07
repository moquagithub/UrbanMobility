"""
Migration v2 — Enrichissement du schéma pour génération PDF.

Ajoute les colonnes nécessaires à data_types, problems, algorithms
sans toucher aux données existantes.

Usage :
    python shared/db/migration_v2.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from shared.db.connection import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s  %(message)s")
log = logging.getLogger("migration_v2")


# ── Colonnes à ajouter ────────────────────────────────────────────────────────

NEW_COLUMNS = {
    "data_types": [
        ("domain",          "VARCHAR(200) NULL COMMENT 'Domaine : trafic, TC, mobilité douce...'"),
        ("data_format",     "VARCHAR(300) NULL COMMENT 'Format : temps-réel, batch, API REST...'"),
        ("sources",         "JSON NULL COMMENT 'Sources typiques de collecte'"),
        ("use_cases",       "JSON NULL COMMENT 'Cas d usage principaux'"),
        ("typical_volume",  "VARCHAR(150) NULL COMMENT 'Volume typique ex: 500k mesures/jour'"),
        ("is_real_time",    "TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = donnée temps-réel'"),
        ("standardization", "TEXT NULL COMMENT 'Normes et standards (DATEX II, GTFS, NeTEx...)'"),
        ("data_challenges", "TEXT NULL COMMENT 'Défis spécifiques à ce type de donnée'"),
    ],
    "problems": [
        ("impact_level",         "ENUM('faible','moyen','élevé','critique') NOT NULL DEFAULT 'moyen'"),
        ("affected_actors",      "JSON NULL COMMENT 'Acteurs impactés par ce problème'"),
        ("detection_indicators", "JSON NULL COMMENT 'Indicateurs permettant de détecter le problème'"),
        ("solutions_overview",   "TEXT NULL COMMENT 'Vue d ensemble des approches de résolution'"),
        ("data_requirements",    "TEXT NULL COMMENT 'Données nécessaires pour traiter ce problème'"),
        ("priority",             "TINYINT NOT NULL DEFAULT 2 COMMENT '1=haute 2=moyenne 3=basse'"),
    ],
    "algorithms": [
        ("math_formulation",  "MEDIUMTEXT NULL COMMENT 'Formulation mathématique en LaTeX'"),
        ("pseudocode",        "MEDIUMTEXT NULL COMMENT 'Pseudo-code étape par étape'"),
        ("input_format",      "TEXT NULL COMMENT 'Format d entrée attendu par l algorithme'"),
        ("output_format",     "TEXT NULL COMMENT 'Format de sortie produit par l algorithme'"),
        ("hyperparameters",   "JSON NULL COMMENT '[{name, type, default, range, description}]'"),
        ("required_libraries","JSON NULL COMMENT '[\"numpy>=1.24\", \"sklearn>=1.3\"]'"),
        ("evaluation_metrics","JSON NULL COMMENT '[{name, formula, interpretation}]'"),
        ("use_case_example",  "TEXT NULL COMMENT 'Exemple concret d application en mobilité urbaine'"),
        ("python_skeleton",   "MEDIUMTEXT NULL COMMENT 'Squelette de code Python fonctionnel'"),
        ("references",        "JSON NULL COMMENT '[{title, authors, year, venue, doi}]'"),
    ],
}


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = %s
          AND COLUMN_NAME  = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return (row[0] if row else 0) > 0


def run_migration() -> None:
    conn = get_connection()
    if not conn:
        log.error("MySQL non disponible — vérifiez .env")
        sys.exit(1)

    total_added = 0
    total_skipped = 0

    try:
        with conn.cursor() as cur:
            for table, columns in NEW_COLUMNS.items():
                log.info("── Table : %s ──────────────────────────────", table)
                for col_name, col_def in columns:
                    if column_exists(cur, table, col_name):
                        log.info("  SKIP  %s.%s (existe déjà)", table, col_name)
                        total_skipped += 1
                    else:
                        sql = f"ALTER TABLE `{table}` ADD COLUMN `{col_name}` {col_def}"
                        cur.execute(sql)
                        log.info("  ✓ ADD  %s.%s", table, col_name)
                        total_added += 1
        log.info("")
        log.info("Migration terminée — %d colonne(s) ajoutée(s), %d ignorée(s)",
                 total_added, total_skipped)
    except Exception as exc:
        log.error("Erreur migration : %s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
