"""
services/distribution_service.py
=================================
Logique de la page Streamlit `page_distributions` + les helpers de figure
`px_distribution` / `px_variable_detail` (app_eda.py), portée en service
pur : au lieu de construire une figure Plotly côté backend, on retourne les
DONNÉES brutes (bins d'histogramme, stats de boxplot, top valeurs) — c'est
le frontend Next.js (DQE-9, Plotly.js) qui se charge du rendu graphique,
conformément au découpage backend/frontend du ticket parent DQE-5.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from app.services import eda_service
from app.services.json_safe import safe_float, safe_float_list

MAX_OUTLIERS_RETOURNES = 200  # garde-fou payload : au-delà, le frontend affichera "+N autres"
N_BINS_MAX = 20               # même valeur que px_distribution() dans app_eda.py
TOP_N_CATEGORIELLES = 20      # même valeur que px_variable_detail() dans app_eda.py


def _reverse_map(meta: Dict[str, Any]) -> Dict[str, str]:
    return {v: k for k, v in meta["mapping"].items()}


def list_columns(dataset_id: str) -> Dict[str, Any]:
    """Équivalent des deux onglets de page_distributions : liste des colonnes numériques / catégorielles."""
    record = eda_service.get_dataset(dataset_id)
    meta   = record["meta"]
    return {
        "dataset_id":    dataset_id,
        "numeriques":    meta["colonnes_numeriques"],
        "categorielles": meta["colonnes_texte"],
    }


def _boxplot_stats(data: pd.Series) -> Dict[str, Any]:
    """
    Stats de boîte à moustaches. Retourne des champs à `None` si `data` est
    vide (colonne 100% manquante) — même comportement que px_distribution()
    dans app_eda.py, qui affichait "Aucune donnée disponible" plutôt que de
    planter sur un NaN.
    """
    if data.empty:
        return {"min": None, "q1": None, "median": None, "q3": None, "max": None, "mean": None, "outliers": []}

    q1, median, q3 = data.quantile([0.25, 0.5, 0.75])
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr
    outliers = data[(data < lower_fence) | (data > upper_fence)]
    return {
        "min":      safe_float(data.min()),
        "q1":       safe_float(q1),
        "median":   safe_float(median),
        "q3":       safe_float(q3),
        "max":      safe_float(data.max()),
        "mean":     safe_float(data.mean()),
        "outliers": safe_float_list(outliers.head(MAX_OUTLIERS_RETOURNES).tolist()),
    }


def get_numeric_distribution(dataset_id: str, column: str) -> Dict[str, Any]:
    """Équivalent de l'onglet 'Variables numériques' de page_distributions pour une colonne donnée."""
    record = eda_service.get_dataset(dataset_id)
    df     = record["df"]
    meta   = record["meta"]

    if column not in meta["colonnes_numeriques"]:
        raise ValueError(f"'{column}' n'est pas une colonne numérique de ce dataset.")

    data = df[column].dropna()

    if data.empty:
        histogramme = {"bin_edges": [], "counts": []}
    else:
        counts, bin_edges = np.histogram(data, bins=min(len(data), N_BINS_MAX) or 1)
        histogramme = {"bin_edges": [float(x) for x in bin_edges], "counts": [int(x) for x in counts]}

    c = meta["colonnes"][column]
    alertes: List[str] = []
    skew = c.get("skewness") or 0
    kurt = c.get("kurtosis") or 0
    if abs(skew) > 1:
        alertes.append(
            f"Distribution asymétrique (skewness={skew:.2f}) — une transformation "
            "logarithmique ou Box-Cox est recommandée."
        )
    if kurt > 3:
        alertes.append(
            f"Distribution leptokurtique (kurtosis={kurt:.2f}) — queues épaisses, "
            "risque d'outliers élevé."
        )
    if data.empty:
        alertes.append("Colonne entièrement vide (0 valeur non manquante) — aucune statistique calculable.")

    return {
        "dataset_id":     dataset_id,
        "colonne":        column,
        "nom_original":   _reverse_map(meta).get(column, column),
        "histogramme":    histogramme,
        "boxplot":        _boxplot_stats(data),
        "moyenne":        safe_float(c.get("moyenne")),
        "mediane":        safe_float(c.get("mediane")),
        "ecart_type":     safe_float(c.get("ecart_type")),
        "skewness":       safe_float(c.get("skewness")),
        "kurtosis":       safe_float(c.get("kurtosis")),
        "alertes":        alertes,
    }


def get_categorical_distribution(dataset_id: str, column: str) -> Dict[str, Any]:
    """Équivalent de l'onglet 'Variables catégorielles' de page_distributions pour une colonne donnée."""
    record = eda_service.get_dataset(dataset_id)
    df     = record["df"]
    meta   = record["meta"]

    if column not in meta["colonnes_texte"]:
        raise ValueError(f"'{column}' n'est pas une colonne catégorielle/texte de ce dataset.")

    c  = meta["colonnes"][column]
    vc = df[column].value_counts().head(TOP_N_CATEGORIELLES)

    return {
        "dataset_id":     dataset_id,
        "colonne":        column,
        "nom_original":   _reverse_map(meta).get(column, column),
        "valeurs_uniques": c["valeurs_uniques"],
        "mode":            c.get("valeur_la_plus_freq"),
        "top_valeurs":     [{"valeur": str(v), "frequence": int(n)} for v, n in vc.items()],
    }
