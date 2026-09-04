"""
routers/normality.py
=====================
Équivalent de page_normality (app_eda.py) :
    GET /api/v1/datasets/{id}/normality           → tests (Shapiro/D'Agostino/Anderson) pour toutes les num.
    GET /api/v1/datasets/{id}/normality/{column}   → Q-Q plot + histogramme + suggestion de transformation

Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import NormalityDetailResponse, NormalitySummaryResponse
from app.services import normality_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-normality"])


@router.get(
    "/{dataset_id}/normality",
    response_model=NormalitySummaryResponse,
    summary="Tests de normalité (Shapiro-Wilk, D'Agostino-Pearson, Anderson-Darling) — toutes variables numériques",
)
async def get_normality_summary(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = normality_service.get_normality_summary(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return NormalitySummaryResponse(**result)


@router.get(
    "/{dataset_id}/normality/{column}",
    response_model=NormalityDetailResponse,
    summary="Détail de normalité d'une variable — Q-Q plot, histogramme vs loi normale, transformation recommandée",
)
async def get_normality_detail(dataset_id: str, column: str, _access=Depends(require_dataset_access)):
    try:
        result = normality_service.get_normality_detail(dataset_id, column)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NormalityDetailResponse(**result)
