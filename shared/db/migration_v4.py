"""
Migration v4 — Table reports (PDF générés par Automatisation 3).
"""
from __future__ import annotations

import logging
from shared.db.connection import get_connection

log = logging.getLogger("shared.db.migration_v4")


def run_migration() -> None:
    conn = get_connection()
    if not conn:
        log.error("Base de données non disponible")
        return
    try:
        with conn.cursor() as cur:
            _create_reports_table(cur)
            _add_report_status_to_enum(cur)
        conn.commit()
        log.info("Migration v4 terminée.")
    finally:
        conn.close()


def _col_exists(cur, table: str, column: str) -> bool:
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
    """, (table, column))
    return cur.fetchone()[0] > 0


def _create_reports_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            data_type_id    VARCHAR(100) COLLATE utf8mb4_unicode_ci NOT NULL,
            report_key      VARCHAR(200) COLLATE utf8mb4_unicode_ci NOT NULL,
            title           VARCHAR(500),
            pdf_path        VARCHAR(1000),
            tex_dir         VARCHAR(1000),
            n_chapters      INT DEFAULT 0,
            n_pages         INT DEFAULT 0,
            compile_success TINYINT(1) DEFAULT 0,
            compile_errors  TEXT,
            compile_rounds  INT DEFAULT 0,
            generation_time_sec FLOAT,
            metrics_summary JSON,
            status          ENUM('pending','generating','compiled','error') DEFAULT 'pending',
            created_at      DATETIME DEFAULT NOW(),
            updated_at      DATETIME DEFAULT NOW() ON UPDATE NOW(),
            UNIQUE KEY uq_report_key (report_key),
            KEY idx_data_type_id (data_type_id),
            CONSTRAINT fk_report_data_type
                FOREIGN KEY (data_type_id) REFERENCES data_types(id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    log.info("Table 'reports' OK.")


def _add_report_status_to_enum(cur) -> None:
    """S'assure que 'report_done' est dans l'ENUM processing_status de data_types."""
    cur.execute("""
        SELECT COLUMN_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='data_types'
          AND COLUMN_NAME='processing_status'
    """)
    row = cur.fetchone()
    if row and "report_done" not in row[0]:
        cur.execute("""
            ALTER TABLE data_types
            MODIFY COLUMN processing_status
            ENUM('pending','catalogue_done','problems_done','algorithms_done',
                 'datasets_done','notebooks_done','report_done')
            DEFAULT 'pending'
        """)
        log.info("ENUM processing_status mis à jour avec 'report_done'.")
    else:
        log.info("ENUM processing_status déjà à jour.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
