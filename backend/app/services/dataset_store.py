"""
services/dataset_store.py
==========================
Couche de PERSISTANCE des datasets — remplace le dict `_DATASETS` en mémoire
du POC (DQE-6).

Décision de conception (DQE-7)
-------------------------------
Persistance par **fichiers pickle sur disque**, un fichier par dataset,
sous `backend/dataset_store/{dataset_id}.pkl`, avec un cache LRU en mémoire
process pour éviter une relecture disque à chaque requête sur un dataset
"chaud".

Pourquoi ce choix plutôt qu'une base de données :
- Le ticket parent (DQE-12, "Conteneurisation / déploiement / CI") n'a pas
  encore statué sur l'infrastructure de prod (Postgres ? Redis ? volume
  partagé ?) — introduire une dépendance DB dans DQE-7 aurait anticipé une
  décision qui n'est pas dans le périmètre de ce sous-ticket.
- Le pattern "écrire des fichiers sous un dossier dédié dans backend/" est
  déjà celui utilisé par le POC pour `UPLOAD_DIR` et `KEYS_DIR`
  (`core/config.py`) — on reste cohérent avec l'existant.
- Aucune nouvelle dépendance externe requise (pas de driver DB, pas de
  service à lancer en local).
- Résout le problème concret identifié dans le README du POC : un
  redémarrage du backend ne doit plus faire perdre les datasets uploadés.

Limites connues (à traiter si besoin dans un ticket ultérieur, ex. DQE-12) :
- Pas d'expiration/nettoyage automatique des fichiers (TTL) — à ajouter si
  le volume de fichiers devient un problème en prod.
- Pas adapté à un déploiement multi-instance sans volume partagé (un
  disque local par instance ne verrait pas les datasets uploadés sur une
  autre instance). Acceptable pour l'instant : DQE-11 (auth) et DQE-12
  (déploiement) n'introduisent pas encore de scaling horizontal.

Ce qui N'EST PAS pickle sur le dataset complet :
- Les objets `ErrorLogger` (eda_analyse.py) et `PIIAnonymizer`
  (pii_anonymizer.py) portent potentiellement un `logging.Logger` avec
  handlers de fichier (RLock non picklable). On n'en extrait donc que les
  données pures nécessaires en aval (`value_log`, `error_count`, `errors`)
  — voir `eda_service.snapshot_error_logger()` / `snapshot_anonymizer()`.
  Tout le reste du record (DataFrames, dict, QuizReport...) ne contient que
  des types picklables.
"""

from __future__ import annotations

import pickle
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.config import DATASET_STORE_DIR

# Taille du cache en mémoire (nombre de datasets "chauds" gardés en RAM
# en plus du fichier sur disque). Au-delà, le moins récemment utilisé est
# évincé du cache (mais reste sur disque).
_CACHE_MAXSIZE = 32

_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_lock = threading.Lock()


class DatasetNotFoundError(Exception):
    """Levée quand un dataset_id ne correspond à aucun dataset stocké (ni cache, ni disque)."""


def _path_for(dataset_id: str) -> Path:
    return DATASET_STORE_DIR / f"{dataset_id}.pkl"


def _cache_put(dataset_id: str, record: Dict[str, Any]) -> None:
    with _lock:
        _cache[dataset_id] = record
        _cache.move_to_end(dataset_id)
        while len(_cache) > _CACHE_MAXSIZE:
            _cache.popitem(last=False)


def save(dataset_id: str, record: Dict[str, Any]) -> None:
    """
    Écrit le record sur disque (remplacement atomique) et met à jour le cache.

    `record` doit être entièrement picklable — voir la note en tête de
    module sur ErrorLogger / PIIAnonymizer.
    """
    path = _path_for(dataset_id)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(fd, "wb") as f:
            pickle.dump(record, f, protocol=pickle.HIGHEST_PROTOCOL)
        Path(tmp_name).replace(path)  # remplacement atomique (même volume)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    _cache_put(dataset_id, record)


def load(dataset_id: str) -> Dict[str, Any]:
    """Récupère un record (cache mémoire, sinon disque). Lève DatasetNotFoundError si absent."""
    with _lock:
        cached = _cache.get(dataset_id)
        if cached is not None:
            _cache.move_to_end(dataset_id)
            return cached

    path = _path_for(dataset_id)
    if not path.exists():
        raise DatasetNotFoundError(
            f"Dataset '{dataset_id}' introuvable. "
            "Il a peut-être expiré, été supprimé, ou l'identifiant est incorrect — "
            "réuploadez le fichier si besoin."
        )
    with open(path, "rb") as f:
        record = pickle.load(f)
    _cache_put(dataset_id, record)
    return record


def exists(dataset_id: str) -> bool:
    if dataset_id in _cache:
        return True
    return _path_for(dataset_id).exists()


def update(dataset_id: str, **fields: Any) -> Dict[str, Any]:
    """
    Charge un record, met à jour certains champs (ex. cache d'un résultat coûteux
    comme le rapport Quiz), le sauvegarde, et retourne le record mis à jour.
    """
    record = load(dataset_id)
    record.update(fields)
    save(dataset_id, record)
    return record


def delete(dataset_id: str) -> None:
    """Supprime un dataset (cache + disque). Pas d'erreur si déjà absent."""
    with _lock:
        _cache.pop(dataset_id, None)
    _path_for(dataset_id).unlink(missing_ok=True)


def get_optional(dataset_id: str, field: str, default: Optional[Any] = None) -> Any:
    """Raccourci pratique : charge un champ précis d'un record, ou `default` s'il est absent."""
    record = load(dataset_id)
    return record.get(field, default)


def list_all_summaries() -> list:
    """
    Liste un résumé léger de tous les datasets sur disque (DQE-11, endpoint
    'mes datasets'). Limite connue : `load()` désérialise le pickle complet
    (DataFrame inclus) pour chaque dataset — acceptable au volume actuel
    (usage interne/pédagogique), mais à revoir (métadonnées stockées à part)
    si le nombre de datasets devient important.
    """
    summaries = []
    for path in DATASET_STORE_DIR.glob("*.pkl"):
        dataset_id = path.stem
        try:
            record = load(dataset_id)
        except Exception:
            continue  # fichier corrompu/partiellement écrit — ignoré plutôt que de faire échouer toute la liste
        summaries.append({
            "dataset_id": dataset_id,
            "filename": record.get("filename"),
            "created_at": record.get("created_at"),
            "source": record.get("source"),
            "nb_lignes": record.get("meta", {}).get("nb_lignes"),
            "nb_colonnes": record.get("meta", {}).get("nb_colonnes"),
        })
    summaries.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return summaries
