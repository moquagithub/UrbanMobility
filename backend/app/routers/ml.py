"""
routers/ml.py
==============
Endpoints ML (DQE-8) — équivalents de app_ml.py, scopés sous
/api/v1/datasets/{dataset_id}/ml/... (voir note de conception dans
ml_service.py : pas d'upload ML séparé, on réutilise les datasets déjà
connus de dataset_store, issus de DQE-6/DQE-7).

Endpoints synchrones (rapides) :
    GET  /ml/data-quality              → assess_data_quality
    GET  /ml/algorithms                → catalogue + classement
    GET  /ml/encoding-plan             → plan d'encodage des catégorielles
    POST /ml/dendrogram                → structure du dendrogramme (borné, donc synchrone)

Endpoints asynchrones (tâches potentiellement longues — clustering, t-SNE, elbow/BIC) :
    POST /ml/k-selection                → 202 + job_id (aide au choix de k)
    POST /ml/clustering                 → 202 + job_id (pipeline complet)
    (statut/résultat : GET /api/v1/ml/jobs/{job_id}, voir routers/ml_jobs.py)

Accès libre : aucune authentification requise.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.models.schemas import (
    AlgorithmsResponse,
    ClusteringRequest,
    DendrogramRequest,
    DendrogramResponse,
    EncodingPlanResponse,
    JobSubmittedResponse,
    KSelectionRequest,
    MLDataQualityResponse,
)
from app.services import ml_job_store, ml_service
from app.services.dataset_access import require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["ml"])


@router.get(
    "/{dataset_id}/ml/data-quality",
    response_model=MLDataQualityResponse,
    summary="Qualité des données pour le clustering — blocages, avertissements, recommandations",
)
async def get_data_quality(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = ml_service.assess_data_quality(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MLDataQualityResponse(**result)


@router.get(
    "/{dataset_id}/ml/algorithms",
    response_model=AlgorithmsResponse,
    summary="Catalogue des 5 algorithmes de clustering, classés par pertinence pour ce dataset",
)
async def get_algorithms(
    dataset_id: str,
    n_features: int | None = Query(None, ge=1, description="Nb de variables sélectionnées pour le clustering (défaut : toutes les num.)"),
    _access=Depends(require_dataset_access),
):
    try:
        result = ml_service.get_algorithms(dataset_id, n_features=n_features)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AlgorithmsResponse(**result)


@router.get(
    "/{dataset_id}/ml/encoding-plan",
    response_model=EncodingPlanResponse,
    summary="Plan d'encodage recommandé pour les variables catégorielles (avant clustering)",
)
async def get_encoding_plan(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = ml_service.get_encoding_plan(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EncodingPlanResponse(**result)


@router.post(
    "/{dataset_id}/ml/dendrogram",
    response_model=DendrogramResponse,
    summary="Structure du dendrogramme (CAH, linkage='ward') — sous-échantillonné, calcul synchrone",
)
async def get_dendrogram(dataset_id: str, body: DendrogramRequest, _access=Depends(require_dataset_access)):
    try:
        result = ml_service.compute_dendrogram(
            dataset_id, body.selected_columns, body.cat_cols, body.scaler_type, max_n=body.max_n
        )
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DendrogramResponse(**result)


@router.post(
    "/{dataset_id}/ml/k-selection",
    response_model=JobSubmittedResponse,
    status_code=202,
    summary="Aide au choix de k — elbow (kmeans/agglomerative) ou BIC/AIC (gmm). Tâche asynchrone.",
)
async def start_k_selection(
    dataset_id: str,
    body: KSelectionRequest,
    background_tasks: BackgroundTasks,
    _access=Depends(require_dataset_access),
):
    job_id = ml_job_store.create_job("k_selection", dataset_id)
    background_tasks.add_task(ml_job_store.run_job, job_id, ml_service.run_k_selection_job, dataset_id, body)
    return JobSubmittedResponse(job_id=job_id, status_url=f"/api/v1/ml/jobs/{job_id}")


@router.post(
    "/{dataset_id}/ml/clustering",
    response_model=JobSubmittedResponse,
    status_code=202,
    summary="Lance le clustering complet (K-Means/DBSCAN/CAH/GMM/Mean-Shift) + PCA/t-SNE/métriques/interprétation. Tâche asynchrone.",
)
async def start_clustering(
    dataset_id: str,
    body: ClusteringRequest,
    background_tasks: BackgroundTasks,
    access=Depends(require_dataset_access),
):
    df = access["df"]
    missing = [c for c in body.selected_columns if c not in df.columns]
    if missing:
        raise HTTPException(status_code=422, detail=f"Colonne(s) inconnue(s) : {missing}")

    job_id = ml_job_store.create_job("clustering", dataset_id)
    background_tasks.add_task(ml_job_store.run_job, job_id, ml_service.run_clustering_job, dataset_id, body)
    return JobSubmittedResponse(job_id=job_id, status_url=f"/api/v1/ml/jobs/{job_id}")
