"""
services/dataset_access.py
============================
Chargement d'un dataset référencé dans l'URL.

L'application est en **accès libre** : aucune authentification, aucun
cloisonnement par propriétaire — tout dataset présent dans `dataset_store`
est consultable par n'importe quel appelant.

`require_dataset_access` reste la dépendance FastAPI utilisée par TOUS les
endpoints sous `/datasets/{dataset_id}/...` : elle centralise le chargement
du dataset et la traduction d'un identifiant inconnu en 404, plutôt que de
dupliquer ce `try/except` dans chaque router.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.services import dataset_store
from app.services.dataset_store import DatasetNotFoundError


def check_dataset_access(dataset_id: str) -> Dict[str, Any]:
    """
    Charge un dataset par son identifiant, ou lève 404.

    Fonction séparée de la dépendance ci-dessous pour les endpoints qui
    référencent un second dataset_id sous un autre nom de paramètre
    (ex. `after_id` dans /reports/synthesis).
    """
    try:
        return dataset_store.load(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def require_dataset_access(dataset_id: str) -> Dict[str, Any]:
    """
    Dépendance FastAPI à utiliser sur TOUT endpoint sous `/datasets/{dataset_id}/...`.
    Retourne le record complet (évite un second chargement dans le service
    appelant), ou lève 404 si le dataset n'existe pas.
    """
    return check_dataset_access(dataset_id)
