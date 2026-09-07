"""
Migration v5 — Table figures (graphiques générés par Auto2).

Stocke les figures PNG (comparaison, métriques) en base :
- bytes raw (figure_data LONGBLOB) + chemin fichier
- métadonnées JSON (métriques par algorithme)
- type de figure (comparison_bar, radar, roc...)
"""
from __future__ import annotations

import logging
from shared.db.connection import get_connection

log = logging.getLogger("shared.db.migration_v5")


def run_migration() -> None:
    conn = get_connection()
    if not conn:
        log.error("Base de données non disponible")
        return
    try:
        with conn.cursor() as cur:
            _create_figures_table(cur)
            _add_datasets_done_status(cur)
        conn.commit()
        log.info("Migration v5 terminée.")
    finally:
        conn.close()


def _create_figures_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS figures (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            data_type_id    VARCHAR(120) COLLATE utf8mb4_unicode_ci NOT NULL,
            figure_key      VARCHAR(200) COLLATE utf8mb4_unicode_ci NOT NULL,
            figure_type     VARCHAR(50)  NOT NULL DEFAULT 'comparison_bar',
            title           VARCHAR(300),
            description     TEXT,
            file_path       VARCHAR(500),
            figure_data     LONGBLOB,
            metrics_json    JSON,
            algorithm_names JSON,
            created_at      DATETIME DEFAULT NOW(),
            updated_at      DATETIME DEFAULT NOW() ON UPDATE NOW(),
            UNIQUE KEY uq_figure_key (figure_key),
            KEY idx_fig_data_type (data_type_id),
            CONSTRAINT fk_figure_data_type
                FOREIGN KEY (data_type_id) REFERENCES data_types(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    log.info("Table 'figures' OK.")


def _add_datasets_done_status(cur) -> None:
    """Ajoute 'datasets_done' dans l'ENUM processing_status si absent."""
    cur.execute("""
        SELECT COLUMN_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='data_types'
          AND COLUMN_NAME='processing_status'
    """)
    row = cur.fetchone()
    if row and "datasets_done" not in row[0]:
        cur.execute("""
            ALTER TABLE data_types
            MODIFY COLUMN processing_status
            ENUM('pending','catalogue_done','problems_done','algorithms_done',
                 'datasets_done','notebooks_done','report_done')
            DEFAULT 'pending'
        """)
        log.info("ENUM processing_status mis à jour avec 'datasets_done'.")
    else:
        log.info("ENUM processing_status déjà à jour.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
