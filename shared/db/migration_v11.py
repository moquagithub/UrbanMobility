"""
Migration v11 — Table quality_issues + colonnes de traçabilité qualité.

Objectif :
  Système de surveillance continue de la cohérence des données entre MySQL
  et MinIO. Chaque problème détecté (skeleton manquant, fichier MinIO absent,
  colonnes incohérentes, notebook en échec…) est enregistré ici avec son
  niveau de sévérité et le résultat de la tentative de réparation automatique.
"""
from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger(__name__)

_DDL_QUALITY_ISSUES = """
CREATE TABLE IF NOT EXISTS quality_issues (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    entity_type     ENUM(
                        'algorithm','problem','data_type',
                        'dataset','notebook','figure','report'
                    ) NOT NULL,
    entity_id       INT          NULL,
    entity_key      VARCHAR(255) NULL,
    data_type_id    VARCHAR(100) NULL,
    issue_type      VARCHAR(100) NOT NULL,
    severity        ENUM('critical','high','medium','low') NOT NULL DEFAULT 'medium',
    description     TEXT         NULL,
    auto_fixable    TINYINT(1)   NOT NULL DEFAULT 0,
    fix_attempted   TINYINT(1)   NOT NULL DEFAULT 0,
    fix_result      ENUM('pending','success','failed','skipped') NULL,
    fix_detail      TEXT         NULL,
    detected_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at     DATETIME     NULL,
    scan_run_id     VARCHAR(36)  NULL,
    INDEX idx_entity   (entity_type, entity_id),
    INDEX idx_severity (severity, fix_attempted),
    INDEX idx_scan     (scan_run_id),
    INDEX idx_type_id  (data_type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_DDL_QUALITY_SCAN_RUNS = """
CREATE TABLE IF NOT EXISTS quality_scan_runs (
    id          VARCHAR(36)  NOT NULL PRIMARY KEY,
    started_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME     NULL,
    mode        ENUM('once','watch','fix') NOT NULL DEFAULT 'once',
    issues_found     INT NOT NULL DEFAULT 0,
    issues_fixed     INT NOT NULL DEFAULT 0,
    issues_failed    INT NOT NULL DEFAULT 0,
    summary     TEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_ALTER_ALGORITHMS = [
    "ALTER TABLE algorithms ADD COLUMN quality_score TINYINT NULL DEFAULT NULL COMMENT '0-100, calculé par le monitor'",
    "ALTER TABLE algorithms ADD COLUMN last_quality_check DATETIME NULL",
]

_ALTER_NOTEBOOKS = [
    "ALTER TABLE notebooks ADD COLUMN quality_score TINYINT NULL DEFAULT NULL",
    "ALTER TABLE notebooks ADD COLUMN last_quality_check DATETIME NULL",
]


def run() -> Dict[str, int]:
    """Applique la migration v11. Idempotent."""
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        return {"ok": 0, "skip": 0, "error": 1}

    stats = {"ok": 0, "skip": 0, "error": 0}

    try:
        with conn.cursor() as cur:
            # Tables principales
            for ddl in [_DDL_QUALITY_ISSUES, _DDL_QUALITY_SCAN_RUNS]:
                try:
                    cur.execute(ddl)
                    stats["ok"] += 1
                    log.info("Table créée/vérifiée : %s", ddl.split()[5])
                except Exception as e:
                    log.warning("DDL erreur : %s", e)
                    stats["error"] += 1

            # Colonnes optionnelles (idempotent via IGNORE ou try/except)
            for sql in _ALTER_ALGORITHMS + _ALTER_NOTEBOOKS:
                try:
                    cur.execute(sql)
                    stats["ok"] += 1
                except Exception:
                    stats["skip"] += 1  # colonne existe déjà

        conn.commit()
    finally:
        conn.close()

    return stats


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    print(run())
