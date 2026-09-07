"""
Repository pour les tables : catalogue_imports + data_types.
Responsabilité unique : lire/écrire les types de données et les imports.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Dict

from shared.db.connection import get_connection, is_available

log = logging.getLogger("shared.db.catalogue")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class CatalogueRepository:

    def is_available(self) -> bool:
        return is_available()

    # ------------------------------------------------------------------ imports

    def record_import(self, file_path: Path, nb_types: int, status: str = "success",
                      notes: str = "") -> Optional[int]:
        """Enregistre un import de catalogue. Retourne l'id de l'import."""
        conn = get_connection()
        if not conn:
            return None
        try:
            file_hash = _hash_file(file_path)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO catalogue_imports (file_name, file_hash, nb_types, status, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (file_path.name, file_hash, nb_types, status, notes or ""),
                )
                return cur.lastrowid
        except Exception as exc:
            log.warning("[CATALOGUE] record_import erreur : %s", exc)
            return None
        finally:
            conn.close()

    def get_last_import(self) -> Optional[Dict]:
        """Retourne le dernier import réussi."""
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM catalogue_imports WHERE status='success' "
                    "ORDER BY imported_at DESC LIMIT 1"
                )
                return cur.fetchone()
        except Exception as exc:
            log.warning("[CATALOGUE] get_last_import erreur : %s", exc)
            return None
        finally:
            conn.close()

    # ------------------------------------------------------------------ data_types

    def upsert_data_type(self, type_id: str, name: str, description: str = "",
                         specifications: str = "", units: str = "", frequency: str = "",
                         import_id: Optional[int] = None,
                         # champs enrichis S1b
                         domain: str = "", data_format: str = "",
                         sources=None, use_cases=None,
                         typical_volume: str = "", is_real_time: int = 0,
                         standardization: str = "", data_challenges: str = "") -> bool:
        """Insère ou met à jour un type de données. Retourne True si succès."""
        import json as _json
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_types
                        (id, name, description, specifications, units, frequency,
                         catalogue_import_id,
                         domain, data_format, sources, use_cases,
                         typical_volume, is_real_time, standardization, data_challenges)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        name                = VALUES(name),
                        description         = VALUES(description),
                        specifications      = VALUES(specifications),
                        units               = VALUES(units),
                        frequency           = VALUES(frequency),
                        catalogue_import_id = VALUES(catalogue_import_id),
                        domain              = COALESCE(NULLIF(VALUES(domain),''), domain),
                        data_format         = COALESCE(NULLIF(VALUES(data_format),''), data_format),
                        sources             = COALESCE(VALUES(sources), sources),
                        use_cases           = COALESCE(VALUES(use_cases), use_cases),
                        typical_volume      = COALESCE(NULLIF(VALUES(typical_volume),''), typical_volume),
                        is_real_time        = VALUES(is_real_time),
                        standardization     = COALESCE(NULLIF(VALUES(standardization),''), standardization),
                        data_challenges     = COALESCE(NULLIF(VALUES(data_challenges),''), data_challenges),
                        updated_at          = NOW()
                    """,
                    (
                        type_id, name, description or "", specifications or "",
                        units or "", frequency or "", import_id,
                        domain or "",
                        data_format or "",
                        _json.dumps(sources, ensure_ascii=False) if sources else None,
                        _json.dumps(use_cases, ensure_ascii=False) if use_cases else None,
                        typical_volume or "",
                        is_real_time,
                        standardization or "",
                        data_challenges or "",
                    ),
                )
            return True
        except Exception as exc:
            log.warning("[CATALOGUE] upsert_data_type erreur (%s) : %s", type_id, exc)
            return False
        finally:
            conn.close()

    def update_enrichment(self, type_id: str, enrichment: Dict) -> bool:
        """Met à jour uniquement les champs d'enrichissement S1b d'un type."""
        import json as _json
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE data_types SET
                        domain          = %s,
                        data_format     = %s,
                        sources         = %s,
                        use_cases       = %s,
                        typical_volume  = %s,
                        is_real_time    = %s,
                        standardization = %s,
                        data_challenges = %s,
                        updated_at      = NOW()
                    WHERE id = %s
                    """,
                    (
                        enrichment.get("domain", "")[:200],
                        enrichment.get("data_format", "")[:300],
                        _json.dumps(enrichment.get("sources", []), ensure_ascii=False),
                        _json.dumps(enrichment.get("use_cases", []), ensure_ascii=False),
                        enrichment.get("typical_volume", "")[:150],
                        1 if enrichment.get("is_real_time") else 0,
                        enrichment.get("standardization", ""),
                        enrichment.get("data_challenges", ""),
                        type_id,
                    ),
                )
            return True
        except Exception as exc:
            log.warning("[CATALOGUE] update_enrichment erreur (%s) : %s", type_id, exc)
            return False
        finally:
            conn.close()

    def list_data_types(self) -> List[Dict]:
        """Retourne tous les types de données avec leur statut de traitement."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM data_types ORDER BY name"
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[CATALOGUE] list_data_types erreur : %s", exc)
            return []
        finally:
            conn.close()

    def get_data_type(self, type_id: str) -> Optional[Dict]:
        conn = get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT * FROM data_types WHERE id = %s", (type_id,))
                return cur.fetchone()
        except Exception as exc:
            log.warning("[CATALOGUE] get_data_type erreur : %s", exc)
            return None
        finally:
            conn.close()

    def update_status(self, type_id: str, status: str) -> bool:
        """Met à jour le processing_status d'un type de données."""
        conn = get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE data_types SET processing_status = %s WHERE id = %s",
                    (status, type_id),
                )
            return True
        except Exception as exc:
            log.warning("[CATALOGUE] update_status erreur : %s", exc)
            return False
        finally:
            conn.close()

    def get_types_by_status(self, status: str) -> List[Dict]:
        """Retourne tous les types ayant un statut donné."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM data_types WHERE processing_status = %s ORDER BY name",
                    (status,),
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[CATALOGUE] get_types_by_status erreur : %s", exc)
            return []
        finally:
            conn.close()

    def get_types_needing_problems(self) -> List[Dict]:
        """Retourne les types dont les problèmes n'ont pas encore été générés."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    """
                    SELECT * FROM data_types
                    WHERE processing_status IN ('pending', 'catalogue_done')
                    ORDER BY name
                    """
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[CATALOGUE] get_types_needing_problems erreur : %s", exc)
            return []
        finally:
            conn.close()

    def get_types_needing_algorithms(self) -> List[Dict]:
        """Retourne les types dont les algorithmes n'ont pas encore été générés."""
        conn = get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(
                    "SELECT * FROM data_types WHERE processing_status = 'problems_done' ORDER BY name"
                )
                return cur.fetchall() or []
        except Exception as exc:
            log.warning("[CATALOGUE] get_types_needing_algorithms erreur : %s", exc)
            return []
        finally:
            conn.close()
