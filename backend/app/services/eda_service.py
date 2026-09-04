"""
services/eda_service.py
========================
Couche service : fait le pont entre les endpoints FastAPI et la logique métier
existante de eda_analyse.py / pii_anonymizer.py (legacy, non réécrite).

Mise à jour DQE-7 :
- La persistance en mémoire (`_DATASETS`) du POC est remplacée par
  `dataset_store` (fichiers pickle sur disque + cache LRU). Voir
  `dataset_store.py` pour la justification du choix.
- Calcul de `imp_df` (compute_importance) dès l'upload, en plus de `meta` :
  plusieurs des nouveaux endpoints EDA (importance, explorateur,
  recommandations) en ont besoin, et il est coûteux à recalculer à chaque
  requête (corrélations sur toutes les colonnes numériques).
- Les objets `ErrorLogger` et `PIIAnonymizer` legacy ne sont plus stockés
  tels quels dans le record persistant (voir `dataset_store.py` : ils
  portent potentiellement un `logging.Logger` non picklable). On en extrait
  un "snapshot" de données pures via `_snapshot_error_logger()` /
  `_snapshot_anonymizer()`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from app.core.config import KEYS_DIR, UPLOAD_DIR, ANON_SALT
from app.services import dataset_store
from app.services.dataset_store import DatasetNotFoundError  # noqa: F401 (ré-exporté pour les routers)

# ── Imports de la logique métier existante (non modifiée) ────────────────────
from pii_anonymizer import load_and_anonymize_v2  # noqa: E402  (legacy, sys.path patché dans config.py)
from eda_analyse import compute_metadata, compute_importance  # noqa: E402


def _snapshot_error_logger(error_logger: Any) -> Dict[str, Any]:
    """
    Extrait uniquement les données pures d'un ErrorLogger (eda_analyse.py) :
    pas de référence au `logging.Logger` interne (non picklable de façon
    fiable — RLock des FileHandler). Le format reste compatible avec ce que
    lisent build_latex()/build_latex_summary() (error_count, errors).
    """
    if error_logger is None:
        return {"error_count": 0, "log_path": None, "errors": []}
    return {
        "error_count": getattr(error_logger, "error_count", 0),
        "log_path": getattr(error_logger, "log_path", None),
        "errors": list(getattr(error_logger, "errors", [])),
    }


def _snapshot_anonymizer(anonymizer: Any) -> Dict[str, Any]:
    """Extrait `value_log` (dict pur) d'un PIIAnonymizer — seule donnée utilisée en aval."""
    if anonymizer is None:
        return {}
    return dict(getattr(anonymizer, "value_log", {}) or {})


def save_upload(file_bytes: bytes, filename: str) -> Path:
    """Écrit le fichier uploadé sur disque dans UPLOAD_DIR sous un nom unique."""
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    dest      = UPLOAD_DIR / safe_name
    dest.write_bytes(file_bytes)
    return dest


def load_and_process(csv_path: Path, original_filename: str) -> Dict[str, Any]:
    """
    Charge le CSV via load_and_anonymize_v2() (anonymisation colonnes + valeurs PII),
    calcule les métadonnées (compute_metadata) et l'importance (compute_importance),
    puis persiste le résultat via dataset_store (fichier pickle + cache).

    Returns:
        dict avec dataset_id + meta + filename (utilisé par la réponse d'upload).
    """
    df, anon_map, anonymizer, err_logger = load_and_anonymize_v2(
        csv_path=str(csv_path),
        salt=ANON_SALT,
        keys_dir=str(KEYS_DIR),
    )
    meta   = compute_metadata(df, anon_map)
    imp_df = compute_importance(df)

    error_snapshot = _snapshot_error_logger(err_logger)
    value_log       = _snapshot_anonymizer(anonymizer)

    # Fermer proprement le logger pour libérer le fichier (cf. patch Windows dans pii_anonymizer.py)
    if hasattr(err_logger, "close"):
        err_logger.close()

    dataset_id = uuid.uuid4().hex
    record: Dict[str, Any] = {
        "df":                 df,
        "anon_map":           anon_map,
        "value_log":          value_log,
        "meta":               meta,
        "imp_df":             imp_df,
        "filename":           original_filename,
        "error_snapshot":     error_snapshot,
        "created_at":         datetime.now().isoformat(timespec="seconds"),
        "source":             "upload",
        "parent_dataset_id":  None,
        "transform_journal":  None,
        "quiz_report":        None,  # calculé à la demande (coûteux), voir quiz_service.py
    }
    dataset_store.save(dataset_id, record)

    return {"dataset_id": dataset_id, "meta": meta, "filename": original_filename}


def register_derived_dataset(
    df: pd.DataFrame,
    anon_map: Dict[str, str],
    filename: str,
    parent_dataset_id: str,
    transform_journal: List[str],
) -> Dict[str, Any]:
    """
    Enregistre un dataset DÉRIVÉ (issu de recommendations_service.apply_recommendations) :
    même structure de record qu'un upload, mais rattaché à un `parent_dataset_id`
    et sans PII value_log / error_snapshot propres (le nouveau df n'a pas été
    ré-anonymisé — il descend d'un dataset déjà anonymisé).

    Ce choix (nouveau dataset_id plutôt que mutation en place) permet la
    comparaison avant/après (report_service.generate_synthesis_report) en
    gardant les deux versions consultables indépendamment.
    """
    meta   = compute_metadata(df, anon_map)
    imp_df = compute_importance(df)

    dataset_id = uuid.uuid4().hex
    parent_record = dataset_store.load(parent_dataset_id)
    record: Dict[str, Any] = {
        "df":                 df,
        "anon_map":           anon_map,
        "value_log":          parent_record.get("value_log", {}),
        "meta":               meta,
        "imp_df":             imp_df,
        "filename":           filename,
        "error_snapshot":     {"error_count": 0, "log_path": None, "errors": []},
        "created_at":         datetime.now().isoformat(timespec="seconds"),
        "source":             "derived",
        "parent_dataset_id":  parent_dataset_id,
        "transform_journal":  transform_journal,
        "quiz_report":        None,
    }
    dataset_store.save(dataset_id, record)
    return {"dataset_id": dataset_id, "meta": meta, "filename": filename}


def get_dataset(dataset_id: str) -> Dict[str, Any]:
    """Récupère un dataset persisté par son id, ou lève DatasetNotFoundError."""
    return dataset_store.load(dataset_id)


def get_overview(dataset_id: str) -> Dict[str, Any]:
    """
    Construit la réponse 'Vue d'ensemble' à partir des métadonnées calculées
    par compute_metadata() (eda_analyse.py) — strictement la même logique
    que celle utilisée pour le rapport LaTeX et la page Streamlit existante.
    """
    record     = get_dataset(dataset_id)
    meta       = record["meta"]
    value_log  = record.get("value_log") or {}

    colonnes = [
        {
            "nom_anonyme":          col,
            "dtype":                info["dtype"],
            "valeurs_manquantes":   info["valeurs_manquantes"],
            "taux_completude":      info["taux_completude"],
            "valeurs_uniques":      info["valeurs_uniques"],
            "valeur_la_plus_freq":  info.get("valeur_la_plus_freq"),
        }
        for col, info in meta["colonnes"].items()
    ]

    pii_count = sum(1 for v in value_log.values() if v["pii_type"] != "none")

    return {
        "dataset_id":               dataset_id,
        "filename":                 record["filename"],
        "date_analyse":             meta["date_analyse"],
        "nb_lignes":                meta["nb_lignes"],
        "nb_colonnes":              meta["nb_colonnes"],
        "valeurs_manquantes_total": meta["valeurs_manquantes_total"],
        "taux_completude_global":   meta["taux_completude_global"],
        "colonnes_numeriques":      len(meta["colonnes_numeriques"]),
        "colonnes_textuelles":      len(meta["colonnes_texte"]),
        "doublons":                 meta["doublons"],
        "colonnes":                 colonnes,
        "pii_colonnes_traitees":    pii_count,
    }
