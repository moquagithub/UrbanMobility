"""
main.py — Point d'entrée de l'API FastAPI.

Lancement local :
    cd backend
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000

Documentation interactive (Swagger) : http://localhost:8000/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import CORS_ALLOWED_ORIGINS
from app.routers import (
    correlation,
    distributions,
    errors,
    explorer,
    importance,
    missing,
    ml,
    ml_jobs,
    normality,
    overview,
    quiz,
    recommendations,
    reports,
)

app = FastAPI(
    title="data-quiz-eda API",
    description=(
        "API exposant le pipeline EDA/ML existant (eda_analyse.py, pii_anonymizer.py, "
        "quiz_processor.py, app_ml.py) pour le frontend Next.js. "
        "DQE-6 : upload CSV + vue d'ensemble. "
        "DQE-7 : services EDA + endpoints d'analyse (manquants, distributions, normalité, "
        "corrélations, importance, quiz, exploration par variable, recommandations, "
        "journal des erreurs, rapports). "
        "DQE-8 : services ML + endpoints de clustering (K-Means, DBSCAN, CAH, GMM, Mean-Shift), "
        "PCA/t-SNE, métriques (silhouette, Calinski-Harabasz, Davies-Bouldin), traitement "
        "asynchrone (BackgroundTasks) avec suivi de statut. "
        "Accès libre : aucune authentification, tous les endpoints sont publics."
    ),
    version="0.5.0-noauth",
)

# CORS — plus aucun cookie n'est échangé (pas d'authentification), donc
# `allow_credentials=False` et un wildcard "*" par défaut suffisent.
# CORS_ALLOWED_ORIGINS reste configurable pour restreindre les origines si
# besoin (voir .env.example).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Datasets EDA/ML (DQE-6 à DQE-10) ─────────────────────────────────────
app.include_router(overview.router)
app.include_router(missing.router)
app.include_router(distributions.router)
app.include_router(normality.router)
app.include_router(correlation.router)
app.include_router(importance.router)
app.include_router(quiz.router)
app.include_router(explorer.router)
app.include_router(recommendations.router)
app.include_router(errors.router)
app.include_router(reports.router)
app.include_router(ml.router)
app.include_router(ml_jobs.router)


@app.get("/api/v1/health", tags=["health"])
async def health_check():
    """Endpoint de vérification — public, utile pour le docker-compose healthcheck (DQE-12)."""
    return {"status": "ok", "service": "data-quiz-eda-api"}
