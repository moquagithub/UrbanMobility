# backend/Dockerfile
# ====================
# Image de production pour l'API FastAPI (DQE-12).
#
# Image simple (pas de multi-stage) : les dépendances (pandas/scipy/scikit-learn/
# matplotlib) ont des wheels manylinux précompilés pour linux/amd64, donc pas
# besoin de compilateur ni d'étape de build séparée — `pip install` reste rapide
# et l'image finale n'a pas de poids mort à éliminer.
FROM python:3.12-slim

WORKDIR /app

# matplotlib doit tourner en headless (pas de serveur X dans le conteneur) —
# voir eda_analyse.py (build_latex génère des figures PNG via matplotlib).
ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Répertoires de données persistantes (voir docker-compose.yml pour les volumes) —
# créés ici pour que le conteneur démarre proprement même sans volume monté
# (ex. `docker run` isolé pour du debug), bien qu'app/core/config.py les
# recrée de toute façon automatiquement si absents.
RUN mkdir -p uploaded_files anonymization_keys dataset_store

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
