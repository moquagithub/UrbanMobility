"""
Repository pour les tables de suivi :
  - llm_calls_log   : trace chaque appel LLM
  - validation_errors : trace chaque échec de validation JSON
  - pipeline_runs   : trace chaque exécution d'automatisation
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.run")


class RunRepository:

    def is_available(self) -> bool:
        return is_available()

    # ------------------------------------------------------------------ llm_calls_log

    def log_llm_call(
        self,
        step: str,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        duration_ms: int,
        success: bool,
        data_type_id: Optional[str] = None,
        problem_id: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> Optional[int]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO llm_calls_log
                        (step, data_type_id, problem_id, provider, model,
                         tokens_in, tokens_out, duration_ms, success, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (step, data_type_id, problem_id, provider, model,
                     tokens_in, tokens_out, duration_ms,
                     1 if success else 0,
                     error_message or None),
                )
                return cur.lastrowid
        except Exception as exc:
            log.warning("[RUN] log_llm_call erreur : %s", exc)
            return None
        finally:
            conn.close()

    def get_llm_stats(self, step: Optional[str] = None) -> Dict:
        """Retourne les stats agrégées des appels LLM (tokens, durée, succès)."""
        conn = get_connection()
        if not conn:
            return {}
        try:
            with conn.cursor(dictionary=True) as cur:
                where = "WHERE step = %s" if step else ""
                params = (step,) if step else ()
                cur.execute(
                    f"""
                    SELECT
                        COUNT(*)                        AS total_calls,
                        SUM(success)                    AS successful,
                        SUM(tokens_in)                  AS total_tokens_in,
                        SUM(tokens_out)                 AS total_tokens_out,
                        AVG(duration_ms)                AS avg_duration_ms,
                        provider
                    FROM llm_calls_log {where}
                    GROUP BY provider
                    """,
                    params,
                )
                return {"by_provider": cur.fetchall() or []}
        except Exception as exc:
            log.warning("[RUN] get_llm_stats erreur : %s", exc)
            return {}
        finally:
            conn.close()

    # ------------------------------------------------------------------ validation_errors

    def log_validation_error(
        self,
        step: str,
        error_type: str,
        error_detail: str,
        raw_response: str = "",
        attempt: int = 1,
        data_type_id: Optional[str] = None,
        problem_id: Optional[int] = None,
    ) -> Optional[int]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO validation_errors
                        (step, data_type_id, problem_id, error_type,
                         error_detail, raw_response, attempt)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (step, data_type_id, problem_id, error_type[:100],
                     error_detail, raw_response[:65535], attempt),
                )
                return cur.lastrowid
        except Exception as exc:
            log.warning("[RUN] log_validation_error erreur : %s", exc)
            return None
        finally:
            conn.close()

    def mark_validation_resolved(self, error_id: int) -> None:
        conn = get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE validation_errors SET resolved = 1 WHERE id = %s", (error_id,)
                )
        except Exception as exc:
            log.warning("[RUN] mark_validation_resolved erreur : %s", exc)
        finally:
            conn.close()

    # ------------------------------------------------------------------ pipeline_runs

    def create_run(
        self,
        project_id: str,
        automation: str = "auto1",
        data_type_id: Optional[str] = None,
    ) -> Optional[int]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pipeline_runs (project_id, automation, data_type_id, status)
                    VALUES (%s, %s, %s, 'running')
                    """,
                    (project_id, automation, data_type_id),
                )
                return cur.lastrowid
        except Exception as exc:
            log.warning("[RUN] create_run erreur : %s", exc)
            return None
        finally:
            conn.close()

    def complete_run(self, run_id: int, result: Dict) -> None:
        conn = get_connection()
        if not conn:
            return
        try:
            import json
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pipeline_runs SET
                        status           = 'completed',
                        completed_at     = NOW(),
                        total_problems   = %s,
                        total_algorithms = %s,
                        result_json      = %s
                    WHERE id = %s
                    """,
                    (
                        result.get("total_problems", 0),
                        result.get("total_algorithms", 0),
                        json.dumps(result, ensure_ascii=False),
                        run_id,
                    ),
                )
        except Exception as exc:
            log.warning("[RUN] complete_run erreur : %s", exc)
        finally:
            conn.close()

    def fail_run(self, run_id: int, reason: str = "") -> None:
        conn = get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pipeline_runs SET
                        status = 'failed', completed_at = NOW()
                    WHERE id = %s
                    """,
                    (run_id,),
                )
        except Exception as exc:
            log.warning("[RUN] fail_run erreur : %s", exc)
        finally:
            conn.close()

    def get_recent_runs(self, limit: int = 20) -> List[Dict]:
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT %s",
                    (limit,),
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[RUN] get_recent_runs erreur : %s", exc)
            return []
        finally:
            conn.close()
