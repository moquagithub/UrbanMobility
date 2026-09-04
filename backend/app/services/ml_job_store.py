"""
services/ml_job_store.py
=========================
Suivi des tâches ML asynchrones (clustering, aide au choix de k). Mécanisme
choisi : **FastAPI `BackgroundTasks` + store en mémoire**, pas de file de
tâches externe (Celery/RQ + Redis).

Justification du choix
------------------------
- `BackgroundTasks` exécute les fonctions synchrones dans le threadpool de
  Starlette (`run_in_threadpool`) : une tâche de clustering longue ne
  bloque donc pas la boucle d'événements — les autres requêtes (y compris
  le polling de statut) continuent d'être servies pendant son exécution.
- Introduire Celery/RQ nécessiterait un broker (Redis/RabbitMQ) qui n'existe
  pas encore dans la stack — décision d'infrastructure qui relève de DQE-12
  (déploiement), pas de ce sous-ticket. Même logique que le choix de
  persistance fichier de DQE-7 (`dataset_store.py`) : ne pas anticiper une
  brique d'infra non encore actée.
- Ce backend tourne en un seul processus à ce stade (pas de scaling
  horizontal) : un store en mémoire process est donc suffisant pour que le
  polling fonctionne de façon fiable.

Limites connues (à revisiter si DQE-12 introduit plusieurs instances/replicas) :
- Pas de persistance disque : un redémarrage du backend perd les jobs en
  cours ou terminés (contrairement aux datasets, qui sont les données
  utilisateur réelles et sont donc persistés dans `dataset_store.py`). Le
  client doit alors resoumettre le job — acceptable pour une tâche dérivée
  et ré-exécutable.
- Un seul processus : le polling ne fonctionne que tant que la requête
  `GET /ml/jobs/{job_id}` arrive sur le même processus que celui qui a
  exécuté le job (vrai pour un déploiement mono-instance).
- Aucune purge automatique des jobs terminés (TTL) — à ajouter si le volume
  devient un problème en prod.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Dict

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


class JobNotFoundError(Exception):
    """Levée quand un job_id ne correspond à aucun job connu."""


def create_job(job_type: str, dataset_id: str) -> str:
    """Crée un job en statut 'pending' et retourne son job_id."""
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "job_type": job_type,
            "dataset_id": dataset_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
    return job_id


def mark_running(job_id: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["started_at"] = datetime.now().isoformat(timespec="seconds")


def mark_completed(job_id: str, result: Dict[str, Any]) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["result"] = result
            _jobs[job_id]["finished_at"] = datetime.now().isoformat(timespec="seconds")


def mark_failed(job_id: str, error: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = error
            _jobs[job_id]["finished_at"] = datetime.now().isoformat(timespec="seconds")


def get_job(job_id: str) -> Dict[str, Any]:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise JobNotFoundError(f"Job '{job_id}' introuvable (jamais créé, ou backend redémarré depuis).")
    return dict(job)  # copie défensive


def run_job(job_id: str, func, *args, **kwargs) -> None:
    """
    Wrapper exécuté par BackgroundTasks : bascule le job en 'running', exécute
    `func`, puis 'completed' avec son retour ou 'failed' avec le message
    d'erreur. `func` doit retourner un dict JSON-safe (voir json_safe.py).
    """
    mark_running(job_id)
    try:
        result = func(*args, **kwargs)
        mark_completed(job_id, result)
    except Exception as exc:  # garde-fou : ne jamais laisser un job bloqué en 'running'
        mark_failed(job_id, str(exc))
