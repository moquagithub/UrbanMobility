"""
Repository pour la table : problems.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional, Dict

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.problems")


class ProblemRepository:

    def is_available(self) -> bool:
        return is_available()

    def save_problems(self, data_type_id: str, problems: List[Dict]) -> Dict[str, int]:
        """
        Insère ou met à jour les problèmes d'un type de données.
        Retourne un dict {problem_key: db_id}.
        """
        conn = get_connection()
        if not conn:
            return {}
        ids: Dict[str, int] = {}
        try:
            with conn.cursor() as cur:
                for p in problems:
                    key = p.get("key", p.get("problem_key", ""))
                    causes = p.get("causes", [])
                    affected_actors      = p.get("affected_actors", [])
                    detection_indicators = p.get("detection_indicators", [])
                    if not isinstance(affected_actors, list):
                        affected_actors = []
                    if not isinstance(detection_indicators, list):
                        detection_indicators = []
                    cur.execute(
                        """
                        INSERT INTO problems
                            (data_type_id, problem_key, title, description,
                             causes, consequences, frequency,
                             impact_level, affected_actors, detection_indicators,
                             solutions_overview, data_requirements, priority)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            title                = VALUES(title),
                            description          = VALUES(description),
                            causes               = VALUES(causes),
                            consequences         = VALUES(consequences),
                            frequency            = VALUES(frequency),
                            impact_level         = VALUES(impact_level),
                            affected_actors      = VALUES(affected_actors),
                            detection_indicators = VALUES(detection_indicators),
                            solutions_overview   = VALUES(solutions_overview),
                            data_requirements    = VALUES(data_requirements),
                            priority             = VALUES(priority)
                        """,
                        (
                            data_type_id,
                            key,
                            p.get("title", "")[:500],
                            p.get("description", ""),
                            json.dumps(causes, ensure_ascii=False),
                            p.get("consequences", ""),
                            p.get("frequency", "courant"),
                            p.get("impact_level", "moyen"),
                            json.dumps(affected_actors, ensure_ascii=False),
                            json.dumps(detection_indicators, ensure_ascii=False),
                            p.get("solutions_overview", ""),
                            p.get("data_requirements", ""),
                            int(p.get("priority", 2)),
                        ),
                    )
                    # Récupère l'id (INSERT ou existing)
                    if cur.lastrowid:
                        ids[key] = cur.lastrowid
                    else:
                        cur.execute(
                            "SELECT id FROM problems WHERE data_type_id=%s AND problem_key=%s",
                            (data_type_id, key),
                        )
                        row = cur.fetchone()
                        if row:
                            ids[key] = row[0]
            log.info("[PROBLEMS] %d problème(s) sauvés pour %s", len(ids), data_type_id)
            return ids
        except Exception as exc:
            log.warning("[PROBLEMS] save_problems erreur (%s) : %s", data_type_id, exc)
            return {}
        finally:
            conn.close()

    def find_problems(self, data_type_id: str) -> List[Dict]:
        """Retourne les problèmes déjà stockés pour un type de données."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM problems WHERE data_type_id = %s ORDER BY problem_key",
                    (data_type_id,),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    if isinstance(r.get("causes"), str):
                        try:
                            r["causes"] = json.loads(r["causes"])
                        except Exception:
                            r["causes"] = []
                return rows
        except Exception as exc:
            log.warning("[PROBLEMS] find_problems erreur : %s", exc)
            return []
        finally:
            conn.close()

    def get_problem_ids(self, data_type_id: str) -> Dict[str, int]:
        """Retourne {problem_key: id} pour un type donné."""
        conn = get_connection()
        if not conn:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT problem_key, id FROM problems WHERE data_type_id = %s",
                    (data_type_id,),
                )
                return {row[0]: row[1] for row in (cur.fetchall() or [])}
        except Exception as exc:
            log.warning("[PROBLEMS] get_problem_ids erreur : %s", exc)
            return {}
        finally:
            conn.close()

    def count_problems(self, data_type_id: str) -> int:
        conn = get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM problems WHERE data_type_id = %s",
                    (data_type_id,),
                )
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception as exc:
            log.warning("[PROBLEMS] count erreur : %s", exc)
            return 0
        finally:
            conn.close()

    def problems_exist(self, data_type_id: str) -> bool:
        return self.count_problems(data_type_id) > 0

    def update_minio_dir(self, problem_id: int, minio_dir: str) -> bool:
        """Stocke le préfixe Minio du répertoire d'un problème (ex: 'traces_gps/problems/p1/')."""
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE problems SET minio_dir = %s WHERE id = %s",
                    (minio_dir, problem_id),
                )
            conn.commit()
            return True
        except Exception as exc:
            log.warning("[PROBLEMS] update_minio_dir erreur (id=%s) : %s", problem_id, exc)
            return False
        finally:
            conn.close()
