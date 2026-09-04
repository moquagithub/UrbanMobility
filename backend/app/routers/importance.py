"""
routers/importance.py
======================
GET /api/v1/datasets/{dataset_id}/importance → équivalent de page_importance (app_eda.py).
Accès libre : aucune authentification requise.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.schemas import ImportanceResponse
from app.services import importance_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-importance"])


@router.get(
    "/{dataset_id}/importance",
    response_model=ImportanceResponse,
    summary="Score composite d'importance des variables + interprétation textuelle générée",
)
async def get_importance(
    dataset_id: str,
    top_n: Optional[int] = Query(None, ge=1, description="Limiter aux N variables les plus importantes (défaut : toutes)"),
    _access=Depends(require_dataset_access),
):
    try:
        result = importance_service.get_importance(dataset_id, top_n=top_n)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ImportanceResponse(**result)
