"""
routers/quiz.py
================
GET /api/v1/datasets/{dataset_id}/quiz → équivalent de page_quiz (app_eda.py).
Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import QuizReportResponse
from app.services import quiz_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-quiz"])


@router.get(
    "/{dataset_id}/quiz",
    response_model=QuizReportResponse,
    summary="Analyse spécifique Quiz/Questionnaire — multi-réponses, JSON range, ordinales, incohérences",
)
async def get_quiz_report(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = quiz_service.get_quiz_report(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return QuizReportResponse(**result)
