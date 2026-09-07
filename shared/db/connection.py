"""
Pool de connexions MySQL partagé entre toutes les automations.

Si DB_HOST est absent du .env, toutes les opérations DB sont silencieusement
ignorées — le pipeline continue en mode dégradé. Aucune exception ne remonte
aux appelants ; les erreurs sont loguées en WARNING.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_pool = None
_db_available: Optional[bool] = None

log = logging.getLogger("shared.db")


def _init_pool() -> bool:
    global _pool, _db_available
    if _db_available is not None:
        return _db_available

    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        log.debug("[DB] DB_HOST non défini — mode sans base de données")
        _db_available = False
        return False

    try:
        import mysql.connector.pooling

        _pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name="shared_mobility",
            pool_size=5,
            host=host,
            port=int(os.environ.get("DB_PORT", "3306")),
            database=os.environ.get("DB_NAME", "urbain_automation"),
            user=os.environ.get("DB_USER", "root"),
            password=os.environ.get("DB_PASSWORD", ""),
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            autocommit=True,
            connection_timeout=10,
        )
        _db_available = True
        log.info("[DB] Pool MySQL créé (%s:%s/%s)",
                 host,
                 os.environ.get("DB_PORT", "3306"),
                 os.environ.get("DB_NAME", "urbain_automation"))
        return True

    except ImportError:
        log.warning("[DB] mysql-connector-python non installé — pip install mysql-connector-python")
        _db_available = False
        return False
    except Exception as exc:
        log.warning("[DB] Connexion impossible — mode dégradé : %s", exc)
        _db_available = False
        return False


def is_available() -> bool:
    return _init_pool()


def get_connection():
    """Retourne une connexion du pool, ou None si la DB est indisponible."""
    if not _init_pool():
        return None
    try:
        return _pool.get_connection()
    except Exception as exc:
        log.warning("[DB] Impossible d'obtenir une connexion : %s", exc)
        return None


def reset():
    """Force la ré-initialisation du pool (utile pour les tests)."""
    global _pool, _db_available
    _pool = None
    _db_available = None
