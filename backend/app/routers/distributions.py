"""
routers/distributions.py
=========================
Équivalent de page_distributions (app_eda.py) :
    GET /api/v1/datasets/{id}/distributions                 → liste des colonnes num./catégorielles
    GET /api/v1/datasets/{id}/distributions/numeric/{col}    → histogramme + boxplot + stats
    GET /api/v1/datasets/{id}/distributions/categorical/{col} → top valeurs + mode

Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import (
    CategoricalDistributionResponse,
    DistributionColumnsResponse,
    NumericDistributionResponse,
)
from app.services import distribution_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-distributions"])


@router.get(
    "/{dataset_id}/distributions",
    response_model=DistributionColumnsResponse,
    summary="Liste des colonnes disponibles pour l'analyse de distribution",
)
async def list_distribution_columns(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = distribution_service.list_columns(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DistributionColumnsResponse(**result)


@router.get(
    "/{dataset_id}/distributions/numeric/{column}",
    response_model=NumericDistributionResponse,
    summary="Distribution d'une variable numérique — histogramme + boxplot + stats descriptives",
)
async def get_numeric_distribution(dataset_id: str, column: str, _access=Depends(require_dataset_access)):
    try:
        result = distribution_service.get_numeric_distribution(dataset_id, column)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NumericDistributionResponse(**result)


@router.get(
    "/{dataset_id}/distributions/categorical/{column}",
    response_model=CategoricalDistributionResponse,
    summary="Distribution d'une variable catégorielle — top 20 valeurs + mode",
)
async def get_categorical_distribution(dataset_id: str, column: str, _access=Depends(require_dataset_access)):
    try:
        result = distribution_service.get_categorical_distribution(dataset_id, column)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CategoricalDistributionResponse(**result)
