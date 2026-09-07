"""
Repository pour la table reports (PDFs générés par Automatisation 3).
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from shared.db.connection import get_connection

log = logging.getLogger("shared.db.reports")


class ReportRepository:

    def find_for_problem(self, data_type_id: str, problem_id: int) -> Optional[Dict]:
        """Retourne le rapport pour un problème donné."""
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM reports WHERE data_type_id=%s AND problem_id=%s "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (data_type_id, problem_id),
                )
                row = cur.fetchone()
                if row and isinstance(row.get("metrics_summary"), str):
                    try:
                        row["metrics_summary"] = json.loads(row["metrics_summary"])
                    except Exception:
                        row["metrics_summary"] = {}
                return row
        except Exception as exc:
            log.warning("[REPORTS] find_for_problem erreur : %s", exc)
            return None
        finally:
            conn.close()

    def report_exists_for_problem(self, data_type_id: str, problem_id: int) -> bool:
        """Vérifie si un rapport compilé existe pour ce problème."""
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM reports WHERE data_type_id=%s AND problem_id=%s "
                    "AND compile_success=1",
                    (data_type_id, problem_id),
                )
                return (cur.fetchone()[0] or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()

    def save_report(
        self,
        data_type_id: str,
        report_key: str,
        title: str = "",
        pdf_path: str = "",
        tex_dir: str = "",
        n_chapters: int = 0,
        n_pages: int = 0,
        compile_success: bool = False,
        compile_errors: str = "",
        compile_rounds: int = 0,
        generation_time_sec: float = 0.0,
        metrics_summary: Optional[Dict] = None,
        status: str = "pending",
        minio_key: str = "",
        problem_id: Optional[int] = None,
    ) -> Optional[int]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reports
                        (data_type_id, problem_id, report_key, title, pdf_path, minio_key,
                         tex_dir, n_chapters, n_pages, compile_success, compile_errors,
                         compile_rounds, generation_time_sec, metrics_summary, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        problem_id           = COALESCE(VALUES(problem_id), problem_id),
                        title                = VALUES(title),
                        pdf_path             = VALUES(pdf_path),
                        minio_key            = COALESCE(NULLIF(VALUES(minio_key),''), minio_key),
                        tex_dir              = VALUES(tex_dir),
                        n_chapters           = VALUES(n_chapters),
                        n_pages              = VALUES(n_pages),
                        compile_success      = VALUES(compile_success),
                        compile_errors       = VALUES(compile_errors),
                        compile_rounds       = VALUES(compile_rounds),
                        generation_time_sec  = VALUES(generation_time_sec),
                        metrics_summary      = VALUES(metrics_summary),
                        status               = VALUES(status),
                        updated_at           = NOW()
                    """,
                    (
                        data_type_id, problem_id, report_key, title[:500], pdf_path, minio_key,
                        tex_dir, n_chapters, n_pages, int(compile_success), compile_errors[:2000],
                        compile_rounds, generation_time_sec,
                        json.dumps(metrics_summary or {}, ensure_ascii=False),
                        status,
                    ),
                )
                if cur.lastrowid:
                    return cur.lastrowid
                cur.execute("SELECT id FROM reports WHERE report_key=%s", (report_key,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as exc:
            log.warning("[REPORTS] save erreur : %s", exc)
            return None
        finally:
            conn.close()

    def find_report(self, data_type_id: str) -> Optional[Dict]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM reports WHERE data_type_id=%s ORDER BY updated_at DESC LIMIT 1",
                    (data_type_id,),
                )
                row = cur.fetchone()
                if row and isinstance(row.get("metrics_summary"), str):
                    try:
                        row["metrics_summary"] = json.loads(row["metrics_summary"])
                    except Exception:
                        row["metrics_summary"] = {}
                return row
        except Exception as exc:
            log.warning("[REPORTS] find erreur : %s", exc)
            return None
        finally:
            conn.close()

    def list_all(self) -> List[Dict]:
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT r.*, dt.name AS type_name
                    FROM reports r
                    JOIN data_types dt ON r.data_type_id = dt.id
                    ORDER BY r.updated_at DESC
                """)
                rows = cur.fetchall() or []
                for row in rows:
                    if isinstance(row.get("metrics_summary"), str):
                        try:
                            row["metrics_summary"] = json.loads(row["metrics_summary"])
                        except Exception:
                            row["metrics_summary"] = {}
                return rows
        except Exception as exc:
            log.warning("[REPORTS] list_all erreur : %s", exc)
            return []
        finally:
            conn.close()

    def report_exists(self, data_type_id: str) -> bool:
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM reports WHERE data_type_id=%s AND compile_success=1",
                    (data_type_id,),
                )
                return (cur.fetchone()[0] or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()
