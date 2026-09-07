"""
Repository pour la table : datasets.
Gestion des datasets synthétiques (génération + stockage + récupération).
Chaque dataset est stocké dans sa propre table MySQL ds_{dataset_key}.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.datasets")


class DatasetRepository:

    def is_available(self) -> bool:
        return is_available()

    def save_dataset(
        self,
        data_type_id: str,
        dataset_key: str,
        data_json: str,
        *,
        problem_id: Optional[int] = None,
        description: str = "",
        n_samples: int = 0,
        n_features: int = 0,
        feature_names: Optional[List[str]] = None,
        schema_json: Optional[List[Dict]] = None,
        stats_json: Optional[Dict] = None,
        file_path: str = "",
        problem_context: str = "",
        generation_method: str = "synthetic_llm",
    ) -> Optional[int]:
        """
        Insère ou met à jour un dataset. Retourne l'ID en base.
        """
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO datasets
                        (data_type_id, problem_id, dataset_key, description,
                         n_samples, feature_names, data_json,
                         n_rows, n_features, generation_method,
                         schema_json, stats_json, file_path, problem_context)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        description       = VALUES(description),
                        n_samples         = VALUES(n_samples),
                        feature_names     = VALUES(feature_names),
                        data_json         = VALUES(data_json),
                        n_rows            = VALUES(n_rows),
                        n_features        = VALUES(n_features),
                        generation_method = VALUES(generation_method),
                        schema_json       = VALUES(schema_json),
                        stats_json        = VALUES(stats_json),
                        file_path         = VALUES(file_path),
                        problem_context   = VALUES(problem_context),
                        updated_at        = NOW()
                    """,
                    (
                        data_type_id, problem_id, dataset_key, description,
                        n_samples,
                        json.dumps(feature_names or [], ensure_ascii=False),
                        data_json,
                        n_samples, n_features, generation_method,
                        json.dumps(schema_json or [], ensure_ascii=False) if schema_json else None,
                        json.dumps(stats_json or {}, ensure_ascii=False) if stats_json else None,
                        file_path,
                        problem_context,
                    ),
                )
                if cur.lastrowid:
                    return cur.lastrowid
                cur.execute(
                    "SELECT id FROM datasets WHERE data_type_id=%s AND dataset_key=%s",
                    (data_type_id, dataset_key),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as exc:
            log.warning("[DATASETS] save erreur (%s/%s) : %s", data_type_id, dataset_key, exc)
            return None
        finally:
            conn.close()

    def find_dataset(self, data_type_id: str, dataset_key: str) -> Optional[Dict]:
        """Retourne un dataset par type + clé."""
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM datasets WHERE data_type_id=%s AND dataset_key=%s",
                    (data_type_id, dataset_key),
                )
                row = cur.fetchone()
                if row:
                    self._deserialize(row)
                return row
        except Exception as exc:
            log.warning("[DATASETS] find erreur : %s", exc)
            return None
        finally:
            conn.close()

    def list_for_type(self, data_type_id: str) -> List[Dict]:
        """Retourne tous les datasets d'un type de données."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM datasets WHERE data_type_id=%s ORDER BY dataset_key",
                    (data_type_id,),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    self._deserialize(r)
                return rows
        except Exception as exc:
            log.warning("[DATASETS] list erreur : %s", exc)
            return []
        finally:
            conn.close()

    def exists(self, data_type_id: str, dataset_key: str) -> bool:
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM datasets WHERE data_type_id=%s AND dataset_key=%s",
                    (data_type_id, dataset_key),
                )
                return (cur.fetchone()[0] or 0) > 0
        except Exception as exc:
            log.warning("[DATASETS] exists erreur : %s", exc)
            return False
        finally:
            conn.close()

    def create_and_populate_table(
        self,
        dataset_key: str,
        df: pd.DataFrame,
        schema_columns: List[Dict],
    ) -> Optional[str]:
        """
        Crée une table MySQL ds_{dataset_key} et insère toutes les lignes du DataFrame.
        Retourne le nom de la table créée, ou None si erreur.
        """
        safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", dataset_key)
        table_name = f"ds_{safe_key}"

        _dtype_map = {
            "datetime":    "DATETIME",
            "categorical": "VARCHAR(255)",
            "string":      "VARCHAR(255)",
            "int":         "INT",
            "integer":     "INT",
            "float":       "DOUBLE",
            "number":      "DOUBLE",
            "bool":        "TINYINT(1)",
            "boolean":     "TINYINT(1)",
        }

        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS `{table_name}`")

                col_defs = ["`_id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY"]
                for col in schema_columns:
                    cname  = col.get("name", "col")
                    cdtype = col.get("dtype", "float").lower()
                    mysql_type = _dtype_map.get(cdtype, "DOUBLE")
                    col_defs.append(f"`{cname}` {mysql_type} NULL")

                cur.execute(
                    f"CREATE TABLE `{table_name}` "
                    f"({', '.join(col_defs)}) "
                    f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                )

                if df.empty:
                    conn.commit()
                    return table_name

                col_names = [c["name"] for c in schema_columns if c["name"] in df.columns]
                cols_sql  = ", ".join(f"`{c}`" for c in col_names)
                placeholders = ", ".join(["%s"] * len(col_names))
                insert_sql = f"INSERT INTO `{table_name}` ({cols_sql}) VALUES ({placeholders})"

                # Convertir les types numpy/pandas en natifs Python
                df_native = df[col_names].copy()
                for c in df_native.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns:
                    df_native[c] = df_native[c].dt.strftime("%Y-%m-%d %H:%M:%S")
                for c in df_native.select_dtypes(include=["bool"]).columns:
                    df_native[c] = df_native[c].astype(int)
                df_native = df_native.where(pd.notnull(df_native), None)

                rows = [
                    [v.item() if isinstance(v, np.generic) else v for v in row]
                    for row in df_native.itertuples(index=False, name=None)
                ]

                BATCH = 500
                for i in range(0, len(rows), BATCH):
                    cur.executemany(insert_sql, rows[i : i + BATCH])

            conn.commit()
            log.info("[DATASETS] Table `%s` créée (%d lignes)", table_name, len(df))
            return table_name

        except Exception as exc:
            log.warning("[DATASETS] create_and_populate_table erreur (%s) : %s", table_name, exc)
            return None
        finally:
            conn.close()

    def update_file_path(self, dataset_id: int, file_path: str) -> None:
        """Met à jour le chemin CSV d'un dataset (après reconstruction)."""
        conn = get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE datasets SET file_path=%s, updated_at=NOW() WHERE id=%s",
                    (file_path, dataset_id),
                )
            conn.commit()
        except Exception as exc:
            log.warning("[DATASETS] update_file_path erreur : %s", exc)
        finally:
            conn.close()

    def update_minio_key(self, dataset_id: int, minio_key: str) -> bool:
        """Stocke la clé/préfixe Minio du dataset (ex: 'traces_gps/problems/p1/dataset/')."""
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE datasets SET minio_key=%s, updated_at=NOW() WHERE id=%s",
                    (minio_key, dataset_id),
                )
            conn.commit()
            return True
        except Exception as exc:
            log.warning("[DATASETS] update_minio_key erreur (id=%s) : %s", dataset_id, exc)
            return False
        finally:
            conn.close()

    def count_for_type(self, data_type_id: str) -> int:
        conn = get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM datasets WHERE data_type_id=%s",
                    (data_type_id,),
                )
                return cur.fetchone()[0] or 0
        except Exception:
            return 0
        finally:
            conn.close()

    @staticmethod
    def _deserialize(row: Dict) -> None:
        for field in ("feature_names", "schema_json", "stats_json"):
            if isinstance(row.get(field), str):
                try:
                    row[field] = json.loads(row[field])
                except Exception:
                    row[field] = []
