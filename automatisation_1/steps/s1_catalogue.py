"""
Step 1 — Lecture du catalogue Excel et upsert dans MySQL.

Entrée  : fichier .xlsx (chemin configurable)
Sortie  : types de données dans la table data_types + catalogue_imports
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import List, Optional

from shared.db.repository import CatalogueRepository
from shared.models import DataType, StepResult

log = logging.getLogger("auto1.s1_catalogue")

_COLUMN_ALIASES = {
    "name":           ["nom_type", "name", "type", "nom", "type_name", "type de donnée",
                       "type de données", "libelle", "libellé"],
    "description":    ["description", "desc", "déscription", "details", "détails", "detail"],
    "specifications": ["specifications", "specs", "spécifications", "caracteristiques",
                       "caractéristiques", "spec"],
    "units":          ["unites", "unités", "units", "unit", "unité"],
    "frequency":      ["frequence", "fréquence", "frequency", "freq", "cadence"],
}


def _find_column(df_columns: List[str], field: str) -> Optional[str]:
    lower_map = {c.lower().strip(): c for c in df_columns}
    for alias in _COLUMN_ALIASES.get(field, []):
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def _make_type_id(name: str) -> str:
    """Génère un identifiant normalisé depuis le nom du type."""
    import re
    slug = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:100] or "type_inconnu"


def run(catalogue_path: Path, force: bool = False) -> StepResult:
    """
    Lit le catalogue Excel et importe les types de données dans MySQL.

    Args:
        catalogue_path : chemin vers le fichier .xlsx
        force          : si True, met à jour même les types déjà présents
    """
    result = StepResult(step="s1_catalogue", success=False)
    log.info("[S1] Démarrage — lecture de %s", catalogue_path)

    # ── Vérification du fichier ──────────────────────────────────────────────
    if not catalogue_path.exists():
        result.add_error(f"Fichier introuvable : {catalogue_path}")
        return result.finish(False)

    # ── Lecture Excel ────────────────────────────────────────────────────────
    try:
        import pandas as pd
        df = pd.read_excel(catalogue_path, engine="openpyxl")
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all")
    except Exception as exc:
        result.add_error(f"Impossible de lire le fichier Excel : {exc}")
        return result.finish(False)

    log.info("[S1] Fichier lu — %d lignes, colonnes : %s", len(df), list(df.columns))

    # ── Détection des colonnes ───────────────────────────────────────────────
    col_name = _find_column(list(df.columns), "name")
    if not col_name:
        result.add_error(
            f"Colonne 'nom' introuvable. Colonnes disponibles : {list(df.columns)}"
        )
        return result.finish(False)

    col_desc  = _find_column(list(df.columns), "description")
    col_spec  = _find_column(list(df.columns), "specifications")
    col_units = _find_column(list(df.columns), "units")
    col_freq  = _find_column(list(df.columns), "frequency")

    # ── Extraction des types ─────────────────────────────────────────────────
    data_types: List[DataType] = []
    for _, row in df.iterrows():
        name = str(row.get(col_name, "")).strip()
        if not name or name.lower() in ("nan", "none", ""):
            continue

        data_types.append(DataType(
            id             = _make_type_id(name),
            name           = name,
            description    = str(row.get(col_desc,  "")).strip() if col_desc  else "",
            specifications = str(row.get(col_spec,  "")).strip() if col_spec  else "",
            units          = str(row.get(col_units, "")).strip() if col_units else "",
            frequency      = str(row.get(col_freq,  "")).strip() if col_freq  else "",
        ))

    if not data_types:
        result.add_error("Aucun type de données valide trouvé dans le catalogue")
        return result.finish(False)

    log.info("[S1] %d type(s) de données extrait(s)", len(data_types))

    # ── Sauvegarde en MySQL ──────────────────────────────────────────────────
    repo = CatalogueRepository()
    if not repo.is_available():
        result.add_warning("MySQL non disponible — les types ne sont pas sauvegardés en base")
        result.data["data_types"] = [vars(dt) for dt in data_types]
        result.data["count"] = len(data_types)
        log.warning("[S1] Mode dégradé (pas de MySQL) — %d types extraits seulement", len(data_types))
        return result.finish(True, f"{len(data_types)} type(s) extraits (MySQL indisponible)")

    # Enregistre l'import
    import_id = repo.record_import(catalogue_path, len(data_types))

    import datetime as _dt
    from shared.storage.catalogue_storage import upload_type_metadata, upload_type_description

    saved = 0
    for dt in data_types:
        ok = repo.upsert_data_type(
            type_id        = dt.id,
            name           = dt.name,
            description    = dt.description,
            specifications = dt.specifications,
            units          = dt.units,
            frequency      = dt.frequency,
            import_id      = import_id,
        )
        if ok:
            saved += 1
            _meta = {
                "id": dt.id,
                "name": dt.name,
                "description": dt.description,
                "specifications": dt.specifications,
                "units": dt.units,
                "frequency": dt.frequency,
                "domain": None,
                "data_format": None,
                "sources": None,
                "use_cases": None,
                "processing_status": "catalogue_done",
                "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
                "pipeline_step": "s1_catalogue",
            }
            _r = upload_type_metadata(dt.id, _meta)
            if not _r.success:
                result.add_warning(f"Minio upload type metadata échoué pour '{dt.name}': {_r.error}")
            _desc = (
                f"Type de données : {dt.name}\n\n"
                f"Description:\n{dt.description}\n\n"
                f"Spécifications:\n{dt.specifications}\n\n"
                f"Unités: {dt.units}\n"
                f"Fréquence: {dt.frequency}\n"
            )
            upload_type_description(dt.id, _desc)
        else:
            result.add_warning(f"Impossible de sauvegarder '{dt.name}'")

    result.data["count"]      = saved
    result.data["import_id"]  = import_id
    result.data["data_types"] = [vars(dt) for dt in data_types]

    log.info("[S1] ✓ %d/%d type(s) sauvés en MySQL", saved, len(data_types))
    return result.finish(True, f"{saved} type(s) de données importés en MySQL")
