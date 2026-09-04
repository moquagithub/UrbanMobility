"""
services/explorer_service.py
=============================
Logique de la page Streamlit `page_variable_explorer` (app_eda.py), portée
en service pur. Réutilise `distribution_service` pour la partie graphique
(histogramme/boxplot ou top valeurs) et `imp_df` (déjà calculé à l'upload
par compute_importance) pour le rang / score / décomposition.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.services import distribution_service, eda_service
from app.services.json_safe import safe_float

# Poids du score composite — identiques à compute_importance() (eda_analyse.py)
_COMPOSANTES = [
    ("Complétude (×0.30)", "completude_pct", 0.30),
    ("Variabilité (×0.35)", "variabilite_norm", 0.35),
    ("Corrélation max (×0.20)", "correlation_max", 0.20),
    ("Unicité (×0.15)", "unicite_norm", 0.15),
]


def list_variables(dataset_id: str) -> Dict[str, Any]:
    """Équivalent du sélecteur de page_variable_explorer : toutes les variables,
    triées par importance décroissante (les variables absentes de imp_df,
    s'il y en a, sont ajoutées à la fin)."""
    record  = eda_service.get_dataset(dataset_id)
    meta    = record["meta"]
    imp_df  = record["imp_df"]
    reverse = {v: k for k, v in meta["mapping"].items()}

    all_vars = list(meta["colonnes"].keys())
    sorted_vars = list(imp_df.index) + [v for v in all_vars if v not in imp_df.index]

    variables: List[Dict[str, Any]] = []
    for col in sorted_vars:
        c = meta["colonnes"][col]
        item = {
            "nom_anonyme":     col,
            "nom_original":    reverse.get(col, col),
            "dtype":           c["dtype"],
            "taux_completude": c["taux_completude"],
            "rang_importance": int(imp_df.loc[col, "rang"]) if col in imp_df.index else None,
            "score_importance": safe_float(imp_df.loc[col, "score_importance"]) if col in imp_df.index else None,
        }
        variables.append(item)

    return {"dataset_id": dataset_id, "variables": variables}


def get_variable_detail(dataset_id: str, column: str) -> Dict[str, Any]:
    """Équivalent de la fiche de page_variable_explorer pour une variable donnée :
    type, complétude, distribution, stats descriptives (si numérique) et
    décomposition du score d'importance (si la variable est dans imp_df)."""
    record  = eda_service.get_dataset(dataset_id)
    df      = record["df"]
    meta    = record["meta"]
    imp_df  = record["imp_df"]
    reverse = {v: k for k, v in meta["mapping"].items()}

    if column not in meta["colonnes"]:
        raise ValueError(f"'{column}' n'existe pas dans ce dataset.")

    c = meta["colonnes"][column]
    is_numeric = pd.api.types.is_numeric_dtype(df[column])

    stats_numeriques: Optional[Dict[str, Optional[float]]] = None
    distribution_numerique = None
    distribution_categorielle = None

    if is_numeric:
        stats_numeriques = {
            "min":        c.get("min"),
            "max":        c.get("max"),
            "moyenne":    c.get("moyenne"),
            "mediane":    c.get("mediane"),
            "ecart_type": c.get("ecart_type"),
            "skewness":   c.get("skewness"),
            "kurtosis":   c.get("kurtosis"),
        }
        distribution_numerique = distribution_service.get_numeric_distribution(dataset_id, column)
    else:
        distribution_categorielle = distribution_service.get_categorical_distribution(dataset_id, column)

    decomposition_score = None
    rang_importance = None
    score_importance = None
    if column in imp_df.index:
        row = imp_df.loc[column]
        rang_importance = int(row["rang"])
        score_importance = safe_float(row["score_importance"])
        decomposition_score = [
            {
                "critere": label,
                "valeur_brute_pct": safe_float(row[key]) or 0.0,
                "contribution_pct": (safe_float(row[key]) or 0.0) * weight,
            }
            for label, key, weight in _COMPOSANTES
        ]

    return {
        "dataset_id":                dataset_id,
        "nom_anonyme":               column,
        "nom_original":              reverse.get(column, column),
        "dtype":                     c["dtype"],
        "taux_completude":           c["taux_completude"],
        "valeurs_uniques":           c["valeurs_uniques"],
        "valeurs_manquantes":        c["valeurs_manquantes"],
        "valeur_la_plus_freq":       c.get("valeur_la_plus_freq"),
        "rang_importance":           rang_importance,
        "score_importance":          score_importance,
        "stats_numeriques":          stats_numeriques,
        "distribution_numerique":    distribution_numerique,
        "distribution_categorielle": distribution_categorielle,
        "decomposition_score":       decomposition_score,
    }
