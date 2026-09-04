"""
routers/explorer.py
====================
Équivalent de page_variable_explorer (app_eda.py) :
    GET /api/v1/datasets/{id}/variables          → liste triée par importance décroissante
    GET /api/v1/datasets/{id}/variables/{column} → fiche détaillée d'une variable

Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import VariableDetailResponse, VariableListResponse
from app.services import explorer_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-explorer"])


@router.get(
    "/{dataset_id}/variables",
    response_model=VariableListResponse,
    summary="Liste des variables, triées par score d'importance décroissant",
)
async def list_variables(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = explorer_service.list_variables(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VariableListResponse(**result)


@router.get(
    "/{dataset_id}/variables/{column}",
    response_model=VariableDetailResponse,
    summary="Fiche détaillée d'une variable — type, distribution, stats, décomposition du score d'importance",
)
async def get_variable_detail(dataset_id: str, column: str, _access=Depends(require_dataset_access)):
    try:
        result = explorer_service.get_variable_detail(dataset_id, column)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VariableDetailResponse(**result)
