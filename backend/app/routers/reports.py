"""
routers/reports.py
===================
Équivalent de page_download (app_eda.py) — génération et téléchargement des
rapports LaTeX + figures, sous forme de ZIP :
    GET /api/v1/datasets/{id}/reports/detailed                    → rapport détaillé (1 dataset)
    GET /api/v1/datasets/{id}/reports/synthesis?after_id=...       → rapport de synthèse avant/après (2 datasets)

Contrairement à app_eda.py (qui exige de ré-uploader le CSV "après" dans un
`st.file_uploader` dédié), le rapport de synthèse utilise directement deux
dataset_id déjà connus de l'API — typiquement `dataset_id` = l'original et
`after_id` = le `new_dataset_id` renvoyé par
`POST /datasets/{id}/recommendations/apply`.

Ces endpoints ne renvoient pas de JSON (pas de `response_model`) : le corps
de la réponse est le ZIP binaire lui-même.

Accès libre : aucune authentification. `dataset_id` est chargé par la
dépendance `require_dataset_access` ; `after_id`, second identifiant nommé
différemment, est chargé manuellement via `check_dataset_access` — les DEUX
datasets doivent exister pour générer la synthèse.
"""

from fastapi import APIRouter, Depends, HTTPException, Response

from app.services import report_service
from app.services.dataset_access import check_dataset_access, require_dataset_access
from app.services.eda_service import DatasetNotFoundError

router = APIRouter(prefix="/api/v1/datasets", tags=["eda-reports"])


@router.get(
    "/{dataset_id}/reports/detailed",
    summary="Rapport détaillé (LaTeX + figures PNG) — retourne un fichier ZIP",
    response_class=Response,
)
async def get_detailed_report(dataset_id: str, _access=Depends(require_dataset_access)):
    try:
        result = report_service.generate_detailed_report(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=result["content"],
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{result["filename"]}"'},
    )


@router.get(
    "/{dataset_id}/reports/synthesis",
    summary="Rapport de synthèse avant/après (LaTeX + figures PNG) — retourne un fichier ZIP",
    response_class=Response,
)
async def get_synthesis_report(dataset_id: str, after_id: str, _access=Depends(require_dataset_access)):
    check_dataset_access(after_id)  # le premier dataset est déjà chargé par require_dataset_access
    try:
        result = report_service.generate_synthesis_report(dataset_id, after_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=result["content"],
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{result["filename"]}"'},
    )
