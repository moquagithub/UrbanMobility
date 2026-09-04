"""
routers/missing.py
===================
GET /api/v1/datasets/{dataset_id}/missing → équivalent de page_missing (app_eda.py).
Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import MissingResponse
from app.services import missing_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-missing"])


@router.get(
    "/{dataset_id}/missing",
    response_model=MissingResponse,
    summary="Valeurs manquantes — détail par variable, trié par complétude croissante",
)
async def get_missing(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = missing_service.get_missing_analysis(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MissingResponse(**result)
