"""
services/missing_service.py
============================
Logique de la page Streamlit `page_missing` (app_eda.py), portée en service
pur (aucune dépendance à `st.*`). Les métriques par colonne proviennent de
`meta["colonnes"]` (compute_metadata, eda_analyse.py) — aucun recalcul de
la logique métier, uniquement mise en forme pour l'API.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.services import eda_service

SEUIL_CRITIQUE_PCT = 20.0  # cf. app_eda.py : seuil critique de 20% de manquants


def _statut(taux_completude: float) -> str:
    if taux_completude >= 80:
        return "ok"
    if taux_completude >= 50:
        return "attention"
    return "critique"


def get_missing_analysis(dataset_id: str) -> Dict[str, Any]:
    """Équivalent de page_missing(df, meta) : détail des manquants par colonne, trié par complétude croissante."""
    record   = eda_service.get_dataset(dataset_id)
    meta     = record["meta"]
    mapping  = meta["mapping"]  # orig -> anon
    reverse  = {v: k for k, v in mapping.items()}

    colonnes: List[Dict[str, Any]] = []
    for col, info in meta["colonnes"].items():
        colonnes.append({
            "variable":         col,
            "nom_original":     reverse.get(col, col),
            "manquants":        info["valeurs_manquantes"],
            "taux_completude":  info["taux_completude"],
            "statut":           _statut(info["taux_completude"]),
        })

    colonnes.sort(key=lambda c: c["taux_completude"])

    return {
        "dataset_id":         dataset_id,
        "seuil_critique_pct": SEUIL_CRITIQUE_PCT,
        "colonnes":           colonnes,
    }
