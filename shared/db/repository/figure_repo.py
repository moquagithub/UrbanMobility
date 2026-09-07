"""
Repository pour la table : figures.
Stockage des graphiques de comparaison générés par Auto2.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.figures")


class FigureRepository:

    def is_available(self) -> bool:
        return is_available()

    def find_for_problem(self, data_type_id: str, problem_id: int) -> Optional[Dict]:
        """Retourne la figure de comparaison pour un problème donné."""
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM figures WHERE data_type_id=%s AND problem_id=%s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (data_type_id, problem_id),
                )
                row = cur.fetchone()
                if row:
                    for f in ("metrics_json", "algorithm_names"):
                        if isinstance(row.get(f), str):
                            try:
                                row[f] = json.loads(row[f])
                            except Exception:
                                pass
                return row
        except Exception as exc:
            log.warning("[FIGURES] find_for_problem erreur : %s", exc)
            return None
        finally:
            conn.close()

    def save_figure(
        self,
        data_type_id: str,
        figure_key: str,
        figure_type: str,
        title: str,
        file_path: str = "",
        figure_data: Optional[bytes] = None,
        metrics_json: Optional[Dict] = None,
        algorithm_names: Optional[List[str]] = None,
        description: str = "",
        minio_key: str = "",
        problem_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Insère ou met à jour une figure. Retourne l'ID.
        minio_key : clé MinIO dans le bucket figures (remplace figure_data en blob).
        """
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO figures
                        (data_type_id, problem_id, figure_key, figure_type, title, description,
                         file_path, figure_data, metrics_json, algorithm_names, minio_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        problem_id      = COALESCE(VALUES(problem_id), problem_id),
                        figure_type     = VALUES(figure_type),
                        title           = VALUES(title),
                        description     = VALUES(description),
                        file_path       = VALUES(file_path),
                        figure_data     = VALUES(figure_data),
                        metrics_json    = VALUES(metrics_json),
                        algorithm_names = VALUES(algorithm_names),
                        minio_key       = COALESCE(NULLIF(VALUES(minio_key),''), minio_key),
                        updated_at      = NOW()
                    """,
                    (
                        data_type_id,
                        problem_id,
                        figure_key,
                        figure_type,
                        title[:299] if title else "",
                        description,
                        file_path,
                        None,
                        json.dumps(metrics_json) if metrics_json else None,
                        json.dumps(algorithm_names) if algorithm_names else None,
                        minio_key,
                    ),
                )
                conn.commit()
                if cur.lastrowid:
                    return cur.lastrowid
                cur.execute("SELECT id FROM figures WHERE figure_key=%s", (figure_key,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as exc:
            log.warning("[FIGURES] save_figure erreur : %s", exc)
            return None
        finally:
            conn.close()

    def find_figures(self, data_type_id: str) -> List[Dict]:
        """Retourne toutes les figures pour un type de données."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT id, data_type_id, figure_key, figure_type, title,
                           description, file_path, metrics_json, algorithm_names,
                           created_at
                    FROM figures WHERE data_type_id=%s ORDER BY created_at
                    """,
                    (data_type_id,),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    for f in ("metrics_json", "algorithm_names"):
                        if isinstance(r.get(f), str):
                            try:
                                r[f] = json.loads(r[f])
                            except Exception:
                                pass
                return rows
        except Exception as exc:
            log.warning("[FIGURES] find_figures erreur : %s", exc)
            return []
        finally:
            conn.close()

    def get_figure_bytes(self, figure_key: str) -> Optional[bytes]:
        """Retourne les bytes PNG d'une figure (pour export)."""
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT figure_data FROM figures WHERE figure_key=%s", (figure_key,)
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as exc:
            log.warning("[FIGURES] get_figure_bytes erreur : %s", exc)
            return None
        finally:
            conn.close()

    def exists(self, figure_key: str) -> bool:
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM figures WHERE figure_key=%s LIMIT 1", (figure_key,))
                return cur.fetchone() is not None
        except Exception:
            return False
        finally:
            conn.close()
