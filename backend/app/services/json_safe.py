"""
services/json_safe.py
======================
Utilitaire partagé : neutralise les NaN/Inf produits par des calculs pandas
(numpy) avant qu'ils n'atteignent la sérialisation JSON.

Pourquoi c'est nécessaire ici
------------------------------
Le vrai jeu de données de test (`community_372_export_...csv`, 250×360)
contient des colonnes numériques **entièrement vides** (0 valeur non
manquante) — dtype float64 mais 100% NaN. `compute_metadata()`
(eda_analyse.py) gère déjà ce cas pour les champs qu'il expose (il retourne
`None` plutôt que NaN). Mais `compute_importance()` (même fichier) a un
angle mort sur ce cas précis : `correlation_max` peut rester `NaN` pour une
colonne entièrement vide, ce qui contamine `score_importance` (NaN au lieu
d'un nombre). Streamlit ne s'en aperçoit pas (il affiche juste "nan" comme
texte) mais Starlette refuse de sérialiser un NaN en JSON strict
(`allow_nan=False`) → 500 en clair.

Conformément au principe de non-réécriture, on NE MODIFIE PAS
`eda_analyse.py` : ce module fait uniquement le pont vers du JSON valide,
à la frontière API — exactement le rôle d'une couche service.
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence


def safe_float(value: Any) -> Optional[float]:
    """Convertit en float, ou None si la valeur est None/NaN/Inf."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def safe_float_list(values: Sequence[Any]) -> List[Optional[float]]:
    return [safe_float(v) for v in values]


def safe_matrix(rows: Sequence[Sequence[Any]]) -> List[List[Optional[float]]]:
    return [safe_float_list(row) for row in rows]
