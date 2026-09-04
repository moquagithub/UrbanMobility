"""
core/config.py — Configuration centrale du backend.

Le dossier `legacy/` contient eda_analyse.py et pii_anonymizer.py tels quels
(copiés depuis le dépôt data-quiz-eda d'origine, non réécrits). Comme
pii_anonymizer.py fait `from eda_analyse import ErrorLogger` (import absolu,
pas relatif), on ajoute legacy/ au sys.path pour que cet import fonctionne
sans toucher au code legacy.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR         = Path(__file__).resolve().parent.parent          # app/
LEGACY_DIR       = BASE_DIR / "legacy"
UPLOAD_DIR       = BASE_DIR.parent / "uploaded_files"                # backend/uploaded_files
KEYS_DIR         = BASE_DIR.parent / "anonymization_keys"             # backend/anonymization_keys — JAMAIS dans Git
DATASET_STORE_DIR = BASE_DIR.parent / "dataset_store"                  # backend/dataset_store — JAMAIS dans Git (DQE-7)

# Charge backend/.env (gitignored) s'il existe — permet un `.env` local sans
# avoir à exporter les variables manuellement à chaque lancement. En prod,
# les variables peuvent aussi être injectées directement par l'environnement
# (Docker, systemd...), auquel cas ce fichier n'est pas nécessaire.
load_dotenv(BASE_DIR.parent / ".env")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
KEYS_DIR.mkdir(parents=True, exist_ok=True)
DATASET_STORE_DIR.mkdir(parents=True, exist_ok=True)

if str(LEGACY_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_DIR))

ANON_SALT = os.environ.get("EDA_ANON_SALT", "dev_salt_change_in_prod")

MAX_UPLOAD_SIZE_MB = 50

# ════════════════════════════════════════════════════════════════════════
# CORS
# ════════════════════════════════════════════════════════════════════════
#
# L'application est en accès libre (aucune authentification, aucun cookie
# échangé) : le wildcard "*" est donc acceptable par défaut. Surchargez
# CORS_ALLOWED_ORIGINS (liste séparée par des virgules) pour restreindre
# les origines autorisées si vous exposez l'API publiquement.

CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "*").split(",") if o.strip()
]
