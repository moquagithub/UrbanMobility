#!/bin/bash
# setup_autostart.sh — À exécuter UNE SEULE FOIS pour configurer l'auto-démarrage de MinIO.
# Après ça, MinIO démarre automatiquement à chaque connexion de l'utilisateur.
# MySQL est déjà configuré pour démarrer au boot (systemctl enabled).

set -e

MINIO_BIN="/home/horhoro/.local/bin/minio"
MINIO_DATA="/home/horhoro/minio-data"
SERVICE_DIR="$HOME/.config/systemd/user"
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.env"

echo "Configuration de MinIO comme service systemd utilisateur..."

if [ ! -f "$MINIO_BIN" ]; then
    echo "ERREUR : MinIO binaire introuvable : $MINIO_BIN"
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "ERREUR : fichier .env introuvable ($ENV_FILE) — créez-le d'abord (voir README, section 4)."
    exit 1
fi

MINIO_ACCESS_KEY="$(grep -m1 '^MINIO_ACCESS_KEY=' "$ENV_FILE" | cut -d= -f2-)"
MINIO_SECRET_KEY="$(grep -m1 '^MINIO_SECRET_KEY=' "$ENV_FILE" | cut -d= -f2-)"

if [ -z "$MINIO_ACCESS_KEY" ] || [ -z "$MINIO_SECRET_KEY" ]; then
    echo "ERREUR : MINIO_ACCESS_KEY / MINIO_SECRET_KEY absentes ou vides dans $ENV_FILE"
    exit 1
fi

mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/minio.service" << EOF
[Unit]
Description=MinIO Object Storage (mobility pipeline)
After=network.target

[Service]
Type=simple
Environment=MINIO_ROOT_USER=$MINIO_ACCESS_KEY
Environment=MINIO_ROOT_PASSWORD=$MINIO_SECRET_KEY
ExecStart=$MINIO_BIN server $MINIO_DATA --address :9000 --console-address :9001
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable minio
systemctl --user start minio

sleep 2

if curl -s http://localhost:9000/minio/health/live > /dev/null 2>&1; then
    echo "✓ MinIO démarré et opérationnel"
else
    echo "⚠ MinIO n'a pas répondu — vérifier : journalctl --user -u minio"
fi

echo "✓ MinIO configuré comme service auto-démarrant (démarre à chaque connexion)"
echo "✓ MySQL déjà auto-démarrant (systemctl enabled au boot)"
echo ""
echo "A partir de maintenant, lancer le pipeline suffit :"
echo "  python3 orchestrator.py"
