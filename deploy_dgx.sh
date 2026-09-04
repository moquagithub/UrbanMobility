#!/usr/bin/env bash
# ==============================================================================
# deploy_dgx.sh — Enterprise DGXspark Deployment Script (Zero-Authorization)
# ==============================================================================
# Automates Docker Compose deployment on NVIDIA DGX Linux (Ubuntu / RHEL).
# Single-origin reverse proxy on port 8080 (or custom $NGINX_PORT).
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "=========================================================================="
echo "   NVIDIA DGXspark — Data Quiz EDA Platform Deployment (Zero-Auth)"
echo "=========================================================================="
echo "Target Host   : $(hostname) ($(uname -s) $(uname -m))"
echo "Working Dir   : ${SCRIPT_DIR}"
echo ""

# 1. Check Docker & Compose
command -v docker >/dev/null 2>&1 || {
    echo "[ERROR] 'docker' is not installed. Please install Docker Engine." >&2
    exit 1
}

if ! docker compose version >/dev/null 2>&1; then
    echo "[ERROR] 'docker compose' (v2) is required." >&2
    exit 1
fi

# 2. Check NVIDIA GPU status (informational on DGX)
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "[INFO] NVIDIA GPU(s) detected on DGX:"
    nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true
    echo ""
else
    echo "[NOTE] 'nvidia-smi' not in PATH; proceeding in containerized mode."
fi

# 3. Environment configuration
if [ ! -f .env ]; then
    echo "[INFO] Creating .env from .env.example..."
    cp .env.example .env
fi

# Read configured port (default 8080)
PORT=$(grep -E '^NGINX_PORT=' .env | cut -d '=' -f2 || echo "8090")
PORT="${PORT:-8090}"

# 4. Build and start services
echo "[INFO] Building and starting Docker containers in detached mode..."
docker compose down --remove-orphans || true
docker compose up --build -d

echo ""
echo "[INFO] Waiting for backend healthcheck (http://127.0.0.1:8000/api/v1/health)..."
HEALTH_URL="http://127.0.0.1:8000/api/v1/health"
MAX_RETRIES=30
RETRY_COUNT=0

until curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ ${RETRY_COUNT} -ge ${MAX_RETRIES} ]; then
        echo "[WARNING] Backend healthcheck timed out. Checking container logs:"
        docker compose logs --tail=30 backend
        break
    fi
    sleep 2
done

if [ ${RETRY_COUNT} -lt ${MAX_RETRIES} ]; then
    echo "[SUCCESS] Backend is healthy!"
fi

PRIMARY_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo ""
echo "=========================================================================="
echo "  EDA Platform Deployed Successfully on DGXspark!"
echo ""
echo "  Entrypoint (Nginx Proxy) : http://${PRIMARY_IP}:${PORT}"
echo "  Local Host Access        : http://localhost:${PORT}"
echo "  API Documentation (docs) : http://localhost:${PORT}/docs"
echo "  Direct Backend Debug     : http://localhost:8000"
echo ""
echo "  Security Model           : ZERO-AUTHORIZATION (Open Access)"
echo "  Status Command           : docker compose ps"
echo "  Live Logs Command        : docker compose logs -f"
echo "=========================================================================="
