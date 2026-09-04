"""
routers/overview.py
====================
Endpoints :
    POST /api/v1/datasets/upload         → upload CSV, anonymisation, retourne dataset_id
    GET  /api/v1/datasets/{id}/overview  → métadonnées de base (page "Vue d'ensemble")
    GET  /api/v1/datasets/mine           → liste de tous les datasets enregistrés

Accès libre : aucun de ces endpoints n'exige d'authentification.

Le reste des analyses (valeurs manquantes, distributions, corrélations...)
est exposé dans les routers dédiés (DQE-7/8).
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.config import MAX_UPLOAD_SIZE_MB
from app.models.schemas import OverviewResponse, UploadResponse
from app.services import dataset_store, eda_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Upload d'un CSV — chargement + anonymisation (colonnes + valeurs PII).",
)
async def upload_csv(
    file: UploadFile = File(..., description="Fichier CSV à analyser"),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers .csv sont acceptés.")

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux ({size_mb:.1f} Mo). Limite : {MAX_UPLOAD_SIZE_MB} Mo.",
        )
    if not content.strip():
        raise HTTPException(status_code=400, detail="Le fichier CSV est vide.")

    saved_path = eda_service.save_upload(content, file.filename)

    try:
        result = eda_service.load_and_process(saved_path, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # garde-fou générique pour le POC
        raise HTTPException(
            status_code=500, detail=f"Erreur lors du traitement du fichier : {exc}"
        ) from exc

    meta = result["meta"]
    return UploadResponse(
        dataset_id=result["dataset_id"],
        filename=result["filename"],
        n_lignes=meta["nb_lignes"],
        n_colonnes=meta["nb_colonnes"],
        separateur_detecte="auto-détecté (voir logs serveur)",
    )


@router.get(
    "/mine",
    summary="Liste de tous les datasets enregistrés (accès libre)",
)
async def list_my_datasets():
    return {"datasets": dataset_store.list_all_summaries()}


@router.get(
    "/{dataset_id}/overview",
    response_model=OverviewResponse,
    summary="Vue d'ensemble — équivalent de la page 'Vue d'ensemble' de app_eda.py",
)
async def get_overview(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        overview = eda_service.get_overview(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return OverviewResponse(**overview)
