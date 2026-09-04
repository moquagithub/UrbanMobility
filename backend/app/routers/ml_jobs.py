"""
routers/ml_jobs.py
===================
Endpoint générique de suivi des tâches ML asynchrones :
    GET /api/v1/ml/jobs/{job_id} → statut + résultat (voir ml_job_store.py)

Volontairement en dehors du préfixe /datasets/{dataset_id}/... : un job_id
identifie la tâche de façon unique, pas besoin du dataset_id dans l'URL
pour le récupérer (il est de toute façon dans la réponse).

Accès libre : aucune authentification, le job_id (UUID) suffit à consulter
le statut et le résultat de la tâche.
"""

from fastapi import APIRouter, HTTPException

from app.models.schemas import JobStatusResponse
from app.services import ml_job_store
from app.services.ml_job_store import JobNotFoundError

router = APIRouter(prefix="/api/v1/ml/jobs", tags=["ml-jobs"])


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Statut et résultat d'une tâche ML asynchrone (clustering ou aide au choix de k)",
)
async def get_job_status(job_id: str):
    try:
        job = ml_job_store.get_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return JobStatusResponse(**job)
