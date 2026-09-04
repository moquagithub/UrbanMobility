"""
services/correlation_service.py
================================
Logique de la page Streamlit `page_correlation` + `px_correlation`
(app_eda.py), portée en service pur : matrice de corrélation de Pearson
(sur les colonnes numériques) et détection des paires fortement corrélées
(|r| ≥ 0.7, seuil identique à l'original).
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from app.services import eda_service
from app.services.json_safe import safe_matrix

SEUIL_FORTE_CORRELATION = 0.7  # identique à app_eda.py


def get_correlation_matrix(dataset_id: str) -> Dict[str, Any]:
    record = eda_service.get_dataset(dataset_id)
    df = record["df"]

    num_df = df.select_dtypes(include="number")
    if len(num_df.columns) < 2:
        raise ValueError("Pas assez de variables numériques pour calculer une corrélation (2 minimum).")

    corr = num_df.corr()
    cols = list(corr.columns)

    toutes_paires: List[Dict[str, Any]] = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if not np.isnan(r):
                toutes_paires.append({
                    "variable_a": cols[i],
                    "variable_b": cols[j],
                    "r_abs": round(float(abs(r)), 4),
                })
    toutes_paires.sort(key=lambda p: p["r_abs"], reverse=True)
    paires_fortes = [p for p in toutes_paires if p["r_abs"] >= SEUIL_FORTE_CORRELATION]

    return {
        "dataset_id": dataset_id,
        "colonnes": cols,
        "matrice": safe_matrix(corr.values),
        "seuil_forte_correlation": SEUIL_FORTE_CORRELATION,
        "paires_fortes": paires_fortes,
        "toutes_paires": toutes_paires,
    }
