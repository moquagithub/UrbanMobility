"""
routers/recommendations.py
===========================
Équivalent de page_recommendations (app_eda.py) :
    GET  /api/v1/datasets/{id}/recommendations        → catalogue d'actions applicables + KPIs
    POST /api/v1/datasets/{id}/recommendations/apply   → applique les actions choisies, crée un NOUVEAU dataset

Voir la note de conception dans recommendations_service.py sur le choix de
créer un dataset dérivé plutôt que de muter le dataset d'origine.
Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import (
    RecommendationsApplyRequest,
    RecommendationsApplyResponse,
    RecommendationsResponse,
)
from app.services import recommendations_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-recommendations"])


@router.get(
    "/{dataset_id}/recommendations",
    response_model=RecommendationsResponse,
    summary="Catalogue des actions de préparation de données applicables (avec justification et code)",
)
async def get_recommendations(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = recommendations_service.get_recommendations(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RecommendationsResponse(**result)


@router.post(
    "/{dataset_id}/recommendations/apply",
    response_model=RecommendationsApplyResponse,
    summary="Applique les actions sélectionnées — crée un nouveau dataset, l'original n'est pas modifié",
)
async def apply_recommendations(dataset_id: str, body: RecommendationsApplyRequest, _access=Depends(require_dataset_access)):
    if not body.action_ids:
        raise HTTPException(status_code=400, detail="Aucune action sélectionnée (action_ids est vide).")
    try:
        result = recommendations_service.apply_recommendations(dataset_id, body.action_ids)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # garde-fou : une transformation (sklearn...) peut échouer sur des données inattendues
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'application des transformations : {exc}") from exc
    return RecommendationsApplyResponse(**result)
