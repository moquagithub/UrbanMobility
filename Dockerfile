# ─────────────────────────────────────────────────────────────────────────────
# BicycleLane – opportunity-detection service
# Target: NVIDIA DGX Spark (GB10 / Blackwell)
#
# Build:  docker build -t bicyclelane:latest .
# Run:    docker run --rm --gpus all -p 9000:9000 bicyclelane:latest
# ─────────────────────────────────────────────────────────────────────────────
FROM nvcr.io/nvidia/pytorch:25.11-py3

# ── System deps for geospatial stack ─────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgdal-dev \
        gdal-bin \
        libgeos-dev \
        libproj-dev \
        libspatialindex-dev \
        wget \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies (cached layer before copying source) ──────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────────────────────
COPY . .

# Install the bicyclelane package itself in editable / src mode
RUN pip install --no-cache-dir -e .

# ── Runtime directories ────────────────────────────────────────────────────────
RUN mkdir -p /app/outputs /app/.cache

# ── Environment defaults ───────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=8 \
    HOST=0.0.0.0 \
    PORT=9000

# ── Expose the web-app port ────────────────────────────────────────────────────
EXPOSE 9000

# ── Entrypoint: FastAPI + uvicorn ──────────────────────────────────────────────
CMD ["sh", "-c", "uvicorn app.main:app --host $HOST --port $PORT --workers 2"]
