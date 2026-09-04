"""
routers/errors.py
==================
GET /api/v1/datasets/{dataset_id}/errors → équivalent de page_errors (app_eda.py).
Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import ErrorLogResponse
from app.services import errors_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-errors"])


@router.get(
    "/{dataset_id}/errors",
    response_model=ErrorLogResponse,
    summary="Journal des lignes ignorées lors du chargement du CSV (erreurs de parsing)",
)
async def get_error_log(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = errors_service.get_error_log(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ErrorLogResponse(**result)
