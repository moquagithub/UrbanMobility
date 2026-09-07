"""
Repository pour la table : algorithms.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional, Dict

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.algorithms")


class AlgorithmRepository:

    def is_available(self) -> bool:
        return is_available()

    def save_algorithms(self, problem_id: int, algorithms: List[Dict]) -> Dict[str, int]:
        """
        Insère ou met à jour les algorithmes d'un problème.
        Retourne {algorithm_key: db_id}.
        """
        conn = get_connection()
        if not conn:
            return {}
        ids: Dict[str, int] = {}
        try:
            with conn.cursor() as cur:
                for alg in algorithms:
                    key = alg.get("key", alg.get("algorithm_key", ""))

                    def _jdump(v, default=None):
                        if v is None:
                            return json.dumps(default if default is not None else [], ensure_ascii=False)
                        if isinstance(v, (list, dict)):
                            return json.dumps(v, ensure_ascii=False)
                        return json.dumps([], ensure_ascii=False)

                    cur.execute(
                        """
                        INSERT INTO algorithms
                            (problem_id, algorithm_key, name, category, principle,
                             formulation, complexity_time, complexity_space,
                             advantages, limitations,
                             math_formulation, pseudocode,
                             input_format, output_format,
                             hyperparameters, required_libraries,
                             evaluation_metrics, use_case_example,
                             python_skeleton, `references`)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                            name               = VALUES(name),
                            category           = VALUES(category),
                            principle          = VALUES(principle),
                            formulation        = VALUES(formulation),
                            complexity_time    = VALUES(complexity_time),
                            complexity_space   = VALUES(complexity_space),
                            advantages         = VALUES(advantages),
                            limitations        = VALUES(limitations),
                            math_formulation   = VALUES(math_formulation),
                            pseudocode         = VALUES(pseudocode),
                            input_format       = VALUES(input_format),
                            output_format      = VALUES(output_format),
                            hyperparameters    = VALUES(hyperparameters),
                            required_libraries = VALUES(required_libraries),
                            evaluation_metrics = VALUES(evaluation_metrics),
                            use_case_example   = VALUES(use_case_example),
                            python_skeleton    = VALUES(python_skeleton),
                            `references`       = VALUES(`references`)
                        """,
                        (
                            problem_id,
                            key,
                            alg.get("name", "")[:300],
                            alg.get("category", "")[:200],
                            alg.get("principle", ""),
                            alg.get("formulation", ""),
                            alg.get("complexity_time", "")[:200],
                            alg.get("complexity_space", "")[:200],
                            _jdump(alg.get("advantages")),
                            _jdump(alg.get("limitations")),
                            alg.get("math_formulation", ""),
                            alg.get("pseudocode", ""),
                            alg.get("input_format", ""),
                            alg.get("output_format", ""),
                            _jdump(alg.get("hyperparameters")),
                            _jdump(alg.get("required_libraries")),
                            _jdump(alg.get("evaluation_metrics")),
                            alg.get("use_case_example", ""),
                            alg.get("python_skeleton", ""),
                            _jdump(alg.get("references")),
                        ),
                    )
                    if cur.lastrowid:
                        ids[key] = cur.lastrowid
                    else:
                        cur.execute(
                            "SELECT id FROM algorithms WHERE problem_id=%s AND algorithm_key=%s",
                            (problem_id, key),
                        )
                        row = cur.fetchone()
                        if row:
                            ids[key] = row[0]
            log.info("[ALGORITHMS] %d algo(s) sauvés pour problem_id=%s", len(ids), problem_id)
            return ids
        except Exception as exc:
            log.warning("[ALGORITHMS] save_algorithms erreur (problem_id=%s) : %s", problem_id, exc)
            return {}
        finally:
            conn.close()

    def find_algorithms(self, problem_id: int) -> List[Dict]:
        """Retourne les algorithmes déjà stockés pour un problème."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM algorithms WHERE problem_id = %s ORDER BY algorithm_key",
                    (problem_id,),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    for field in ("advantages", "limitations"):
                        if isinstance(r.get(field), str):
                            try:
                                r[field] = json.loads(r[field])
                            except Exception:
                                r[field] = []
                return rows
        except Exception as exc:
            log.warning("[ALGORITHMS] find_algorithms erreur : %s", exc)
            return []
        finally:
            conn.close()

    def find_all_for_data_type(self, data_type_id: str) -> List[Dict]:
        """Retourne tous les algorithmes d'un type de données (via JOIN)."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT a.*, p.problem_key, p.title AS problem_title, p.data_type_id
                    FROM algorithms a
                    JOIN problems p ON a.problem_id = p.id
                    WHERE p.data_type_id = %s
                    ORDER BY p.problem_key, a.algorithm_key
                    """,
                    (data_type_id,),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    for field in ("advantages", "limitations"):
                        if isinstance(r.get(field), str):
                            try:
                                r[field] = json.loads(r[field])
                            except Exception:
                                r[field] = []
                return rows
        except Exception as exc:
            log.warning("[ALGORITHMS] find_all_for_data_type erreur : %s", exc)
            return []
        finally:
            conn.close()

    def algorithms_exist(self, problem_id: int) -> bool:
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM algorithms WHERE problem_id = %s", (problem_id,)
                )
                row = cur.fetchone()
                return (row[0] if row else 0) > 0
        except Exception as exc:
            log.warning("[ALGORITHMS] algorithms_exist erreur : %s", exc)
            return False
        finally:
            conn.close()

    def update_minio_dir(self, algorithm_id: int, minio_dir: str) -> bool:
        """Stocke le préfixe Minio du répertoire d'un algorithme."""
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE algorithms SET minio_dir = %s WHERE id = %s",
                    (minio_dir, algorithm_id),
                )
            conn.commit()
            return True
        except Exception as exc:
            log.warning("[ALGORITHMS] update_minio_dir erreur (id=%s) : %s", algorithm_id, exc)
            return False
        finally:
            conn.close()

    def update_skeleton(self, algorithm_id: int, python_skeleton: str) -> bool:
        """Remplace le python_skeleton d'un algorithme (réparation post-validation)."""
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE algorithms SET python_skeleton = %s WHERE id = %s",
                    (python_skeleton, algorithm_id),
                )
            conn.commit()
            return True
        except Exception as exc:
            log.warning("[ALGORITHMS] update_skeleton erreur (id=%s) : %s", algorithm_id, exc)
            return False
        finally:
            conn.close()

    def get_existing_names(self, data_type_id: str) -> List[str]:
        """Retourne les noms d'algorithmes déjà générés pour un type (pour éviter doublons)."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.name FROM algorithms a
                    JOIN problems p ON a.problem_id = p.id
                    WHERE p.data_type_id = %s
                    """,
                    (data_type_id,),
                )
                return [row[0] for row in (cur.fetchall() or [])]
        except Exception as exc:
            log.warning("[ALGORITHMS] get_existing_names erreur : %s", exc)
            return []
        finally:
            conn.close()
