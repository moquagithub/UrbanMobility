"""
Repository pour les tables de traçabilité :
  - llm_calls_log   : chaque appel LLM
  - validation_errors : chaque erreur de validation JSON
"""
from __future__ import annotations

import logging
from typing import List, Optional, Dict

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.logs")


class LogRepository:

    def is_available(self) -> bool:
        return is_available()

    # ------------------------------------------------------------------ LLM calls

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
        """Enregistre un appel LLM. Retourne l'id de la ligne insérée."""
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
                    (
                        step,
                        data_type_id,
                        problem_id,
                        provider,
                        model,
                        tokens_in,
                        tokens_out,
                        duration_ms,
                        1 if success else 0,
                        error_message,
                    ),
                )
                return cur.lastrowid
        except Exception as exc:
            log.warning("[LOG] log_llm_call erreur : %s", exc)
            return None
        finally:
            conn.close()

    def get_llm_stats(self, step: Optional[str] = None) -> Dict:
        """Statistiques globales sur les appels LLM."""
        conn = get_connection()
        if not conn:
            return {}
        try:
            where = "WHERE step = %s" if step else ""
            params = (step,) if step else ()
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    f"""
                    SELECT
                        COUNT(*)        AS total_calls,
                        SUM(success)    AS successful,
                        SUM(tokens_in)  AS total_tokens_in,
                        SUM(tokens_out) AS total_tokens_out,
                        AVG(duration_ms) AS avg_duration_ms,
                        provider,
                        COUNT(*) AS calls_by_provider
                    FROM llm_calls_log {where}
                    GROUP BY provider
                    """,
                    params,
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[LOG] get_llm_stats erreur : %s", exc)
            return {}
        finally:
            conn.close()

    # ------------------------------------------------------------------ validation errors

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
        """Enregistre une erreur de validation."""
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
                    (
                        step,
                        data_type_id,
                        problem_id,
                        error_type[:100],
                        error_detail,
                        raw_response[:65535] if raw_response else "",
                        attempt,
                    ),
                )
                return cur.lastrowid
        except Exception as exc:
            log.warning("[LOG] log_validation_error erreur : %s", exc)
            return None
        finally:
            conn.close()

    def mark_resolved(self, error_id: int) -> bool:
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE validation_errors SET resolved = 1 WHERE id = %s", (error_id,)
                )
            return True
        except Exception as exc:
            log.warning("[LOG] mark_resolved erreur : %s", exc)
            return False
        finally:
            conn.close()

    def get_recent_errors(self, limit: int = 50) -> List[Dict]:
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM validation_errors ORDER BY occurred_at DESC LIMIT %s",
                    (limit,),
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[LOG] get_recent_errors erreur : %s", exc)
            return []
        finally:
            conn.close()
