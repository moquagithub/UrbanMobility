"""
Migration v3 — Automatisation 2 : datasets enrichis + table notebooks + résultats.

Usage :
    python shared/db/migration_v3.py
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
log = logging.getLogger("migration_v3")


# ── Nouvelles colonnes sur tables existantes ──────────────────────────────────

NEW_COLUMNS = {
    "datasets": [
        ("n_rows",             "INT NOT NULL DEFAULT 0 COMMENT 'Nombre de lignes'"),
        ("n_features",         "INT NOT NULL DEFAULT 0 COMMENT 'Nombre de colonnes'"),
        ("generation_method",  "VARCHAR(100) NOT NULL DEFAULT 'synthetic_llm'"),
        ("schema_json",        "JSON NULL COMMENT 'Schéma colonnes [{name,dtype,description,params}]'"),
        ("stats_json",         "JSON NULL COMMENT 'Statistiques descriptives par colonne'"),
        ("file_path",          "VARCHAR(500) NULL COMMENT 'Chemin fichier CSV sur disque'"),
        ("problem_context",    "TEXT NULL COMMENT 'Contexte du problème simulé dans ce dataset'"),
    ],
    "notebook_results": [
        ("notebook_id",         "INT NULL COMMENT 'FK vers table notebooks'"),
        ("output_figures",      "JSON NULL COMMENT 'Chemins des figures générées'"),
        ("stdout_summary",      "TEXT NULL"),
        ("final_metrics_json",  "JSON NULL COMMENT 'Métriques extraites des cellules de sortie'"),
    ],
}


# ── Nouvelle table notebooks ──────────────────────────────────────────────────

CREATE_NOTEBOOKS = """
CREATE TABLE IF NOT EXISTS notebooks (
    id                 INT          NOT NULL AUTO_INCREMENT,
    data_type_id       VARCHAR(100) NOT NULL,
    problem_id         INT          NOT NULL,
    algorithm_id       INT          NOT NULL,
    notebook_key       VARCHAR(200) NOT NULL COMMENT 'nb_{type}_{prob}_{alg}',
    title              VARCHAR(500) NOT NULL,
    notebook_json      LONGTEXT     NOT NULL COMMENT 'Contenu .ipynb complet',
    notebook_path      VARCHAR(500) NULL     COMMENT 'Chemin .ipynb source',
    executed_path      VARCHAR(500) NULL     COMMENT 'Chemin .ipynb exécuté (avec outputs)',
    status             ENUM('generated','running','executed','failed')
                                    NOT NULL DEFAULT 'generated',
    execution_time_sec FLOAT        NULL,
    error_message      TEXT         NULL,
    last_executed_at   DATETIME     NULL,
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE  KEY uq_notebook_key  (notebook_key),
    INDEX   idx_nb_data_type    (data_type_id),
    INDEX   idx_nb_problem      (problem_id),
    INDEX   idx_nb_algorithm    (algorithm_id),
    INDEX   idx_nb_status       (status),
    CONSTRAINT fk_nb2_dtype
        FOREIGN KEY (data_type_id) REFERENCES data_types(id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_nb2_problem
        FOREIGN KEY (problem_id) REFERENCES problems(id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_nb2_algorithm
        FOREIGN KEY (algorithm_id) REFERENCES algorithms(id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, column),
    )
    return (cur.fetchone()[0] or 0) > 0


def table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
        (table,),
    )
    return (cur.fetchone()[0] or 0) > 0


def run_migration() -> None:
    conn = get_connection()
    if not conn:
        log.error("MySQL non disponible")
        sys.exit(1)

    added = skipped = 0
    try:
        with conn.cursor() as cur:
            # Colonnes sur tables existantes
            for table, columns in NEW_COLUMNS.items():
                log.info("── Table %s ──", table)
                for col_name, col_def in columns:
                    if column_exists(cur, table, col_name):
                        log.info("  SKIP  %s.%s", table, col_name)
                        skipped += 1
                    else:
                        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{col_name}` {col_def}")
                        log.info("  ✓ ADD  %s.%s", table, col_name)
                        added += 1

            # Table notebooks
            log.info("── Table notebooks ──")
            if table_exists(cur, "notebooks"):
                log.info("  SKIP  notebooks (existe déjà)")
                skipped += 1
            else:
                cur.execute(CREATE_NOTEBOOKS)
                log.info("  ✓ CREATE  notebooks")
                added += 1

        log.info("")
        log.info("Migration v3 terminée — %d ajouté(s), %d ignoré(s)", added, skipped)
    except Exception as exc:
        log.error("Erreur : %s", exc)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
