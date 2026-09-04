"""
routers/correlation.py
=======================
GET /api/v1/datasets/{dataset_id}/correlation → équivalent de page_correlation (app_eda.py).
Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import CorrelationResponse
from app.services import correlation_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-correlation"])


@router.get(
    "/{dataset_id}/correlation",
    response_model=CorrelationResponse,
    summary="Matrice de corrélation de Pearson + paires fortement corrélées (|r| ≥ 0.7)",
)
async def get_correlation(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = correlation_service.get_correlation_matrix(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CorrelationResponse(**result)
