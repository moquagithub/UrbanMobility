"""
Repository pour les tables : notebooks + notebook_results.
Gestion des notebooks Jupyter générés et exécutés.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.notebooks")


class NotebookRepository:

    def is_available(self) -> bool:
        return is_available()

    # ── notebooks ─────────────────────────────────────────────────────────────

    def notebook_exists_for_problem(self, data_type_id: str, problem_id: int) -> bool:
        """Vérifie si un notebook de comparaison existe déjà pour ce problème."""
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM notebooks WHERE data_type_id=%s AND problem_id=%s "
                    "AND (algorithm_id IS NULL OR notebook_key LIKE '%%_comparison')",
                    (data_type_id, problem_id),
                )
                return (cur.fetchone()[0] or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()

    def list_for_problem(self, data_type_id: str, problem_id: int) -> List[Dict]:
        """Retourne les notebooks de comparaison pour un problème donné."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM notebooks WHERE data_type_id=%s AND problem_id=%s ORDER BY id",
                    (data_type_id, problem_id),
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[NOTEBOOKS] list_for_problem erreur : %s", exc)
            return []
        finally:
            conn.close()

    def save_notebook(
        self,
        data_type_id: str,
        problem_id: int,
        notebook_key: str,
        title: str,
        algorithm_id: Optional[int] = None,
        notebook_json: str = "",
        notebook_path: str = "",
        minio_key: str = "",
        n_algorithms: Optional[int] = None,
    ) -> Optional[int]:
        """Insère ou met à jour un notebook. Retourne l'ID."""
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notebooks
                        (data_type_id, problem_id, algorithm_id, n_algorithms,
                         notebook_key, title, notebook_json, notebook_path, minio_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        title          = VALUES(title),
                        n_algorithms   = VALUES(n_algorithms),
                        notebook_json  = VALUES(notebook_json),
                        notebook_path  = VALUES(notebook_path),
                        minio_key      = COALESCE(NULLIF(VALUES(minio_key),''), minio_key),
                        status         = 'generated',
                        error_message  = NULL,
                        updated_at     = NOW()
                    """,
                    (data_type_id, problem_id, algorithm_id, n_algorithms,
                     notebook_key, title[:500], notebook_json, notebook_path, minio_key),
                )
                if cur.lastrowid:
                    return cur.lastrowid
                cur.execute(
                    "SELECT id FROM notebooks WHERE notebook_key=%s",
                    (notebook_key,),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as exc:
            log.warning("[NOTEBOOKS] save erreur (%s) : %s", notebook_key, exc)
            return None
        finally:
            conn.close()

    def update_execution(
        self,
        notebook_id: int,
        status: str,
        execution_time_sec: Optional[float] = None,
        error_message: Optional[str] = None,
        executed_path: Optional[str] = None,
        executed_minio_key: Optional[str] = None,
    ) -> bool:
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE notebooks SET
                        status               = %s,
                        execution_time_sec   = %s,
                        error_message        = %s,
                        executed_path        = COALESCE(%s, executed_path),
                        executed_minio_key   = COALESCE(%s, executed_minio_key),
                        last_executed_at     = NOW(),
                        updated_at           = NOW()
                    WHERE id = %s
                    """,
                    (status, execution_time_sec, error_message,
                     executed_path, executed_minio_key, notebook_id),
                )
            return True
        except Exception as exc:
            log.warning("[NOTEBOOKS] update_execution erreur : %s", exc)
            return False
        finally:
            conn.close()

    def find_by_key(self, notebook_key: str) -> Optional[Dict]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM notebooks WHERE notebook_key=%s", (notebook_key,))
                return cur.fetchone()
        except Exception as exc:
            log.warning("[NOTEBOOKS] find_by_key erreur : %s", exc)
            return None
        finally:
            conn.close()

    def list_for_type(self, data_type_id: str) -> List[Dict]:
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT n.*, p.problem_key, p.title AS problem_title,
                           a.algorithm_key, a.name AS algorithm_name
                    FROM notebooks n
                    LEFT JOIN problems p ON n.problem_id = p.id
                    LEFT JOIN algorithms a ON n.algorithm_id = a.id
                    WHERE n.data_type_id = %s
                    ORDER BY p.problem_key, a.algorithm_key
                    """,
                    (data_type_id,),
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[NOTEBOOKS] list_for_type erreur : %s", exc)
            return []
        finally:
            conn.close()

    def count_by_status(self, data_type_id: str, status: str) -> int:
        conn = get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM notebooks WHERE data_type_id=%s AND status=%s",
                    (data_type_id, status),
                )
                return cur.fetchone()[0] or 0
        except Exception:
            return 0
        finally:
            conn.close()

    def notebook_exists(self, notebook_key: str) -> bool:
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM notebooks WHERE notebook_key=%s",
                    (notebook_key,),
                )
                return (cur.fetchone()[0] or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()

    def list_pending_execution(self, data_type_id: Optional[str] = None) -> List[Dict]:
        """Retourne les notebooks générés mais pas encore exécutés."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                if data_type_id:
                    cur.execute(
                        "SELECT * FROM notebooks WHERE status='generated' AND data_type_id=%s ORDER BY id",
                        (data_type_id,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM notebooks WHERE status='generated' ORDER BY id"
                    )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[NOTEBOOKS] list_pending erreur : %s", exc)
            return []
        finally:
            conn.close()

    # ── notebook_results ──────────────────────────────────────────────────────

    def save_result(
        self,
        run_id: int,
        notebook_id: int,
        algorithm_id: int,
        notebook_num: int,
        notebook_path: str = "",
        status: str = "success",
        execution_time_sec: float = 0.0,
        metrics_json: Optional[Dict] = None,
        final_metrics_json: Optional[Dict] = None,
        output_figures: Optional[List[str]] = None,
        stdout_summary: str = "",
        cell_errors_count: int = 0,
        figure_path: str = "",
    ) -> Optional[int]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notebook_results
                        (run_id, algorithm_id, notebook_num, notebook_path,
                         figure_path, metrics_json, execution_time_sec, status,
                         cell_errors_count, notebook_id, output_figures,
                         stdout_summary, final_metrics_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        run_id, algorithm_id, notebook_num, notebook_path,
                        figure_path,
                        json.dumps(metrics_json or {}, ensure_ascii=False),
                        execution_time_sec, status, cell_errors_count,
                        notebook_id,
                        json.dumps(output_figures or [], ensure_ascii=False),
                        stdout_summary[:2000] if stdout_summary else "",
                        json.dumps(final_metrics_json or {}, ensure_ascii=False),
                    ),
                )
                return cur.lastrowid
        except Exception as exc:
            log.warning("[NOTEBOOKS] save_result erreur : %s", exc)
            return None
        finally:
            conn.close()

    def flag_skeleton_regen(self, algorithm_id: Optional[int], reason: str = "") -> bool:
        """
        Marque un algorithme comme nécessitant une régénération de squelette.
        Met à jour algorithms.needs_skeleton_regen=1 et stocke la raison.
        """
        if not algorithm_id:
            return False
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE algorithms
                    SET needs_skeleton_regen = 1,
                        skeleton_regen_reason = %s
                    WHERE id = %s
                    """,
                    (reason[:500] if reason else "", algorithm_id),
                )
            return True
        except Exception as exc:
            log.warning("[NOTEBOOKS] flag_skeleton_regen erreur algo_id=%s : %s", algorithm_id, exc)
            return False
        finally:
            conn.close()

    def get_results_for_type(self, data_type_id: str) -> List[Dict]:
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT nr.*, n.notebook_key, n.title AS notebook_title,
                           a.name AS algorithm_name,
                           p.title AS problem_title, p.problem_key
                    FROM notebook_results nr
                    JOIN notebooks n ON nr.notebook_id = n.id
                    JOIN algorithms a ON nr.algorithm_id = a.id
                    LEFT JOIN problems p ON n.problem_id = p.id
                    WHERE n.data_type_id = %s
                    ORDER BY p.problem_key, a.algorithm_key
                    """,
                    (data_type_id,),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    for f in ("metrics_json", "final_metrics_json", "output_figures"):
                        if isinstance(r.get(f), str):
                            try:
                                r[f] = json.loads(r[f])
                            except Exception:
                                r[f] = {}
                return rows
        except Exception as exc:
            log.warning("[NOTEBOOKS] get_results erreur : %s", exc)
            return []
        finally:
            conn.close()
