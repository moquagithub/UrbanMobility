"""
services/errors_service.py
===========================
Logique de la page Streamlit `page_errors` (app_eda.py), portée en service
pur. Contrairement au POC, on ne lit plus un objet `ErrorLogger` vivant
(non picklable en l'état, cf. dataset_store.py) mais son snapshot
(`error_snapshot`) constitué à l'upload par `eda_service._snapshot_error_logger()`.
"""

from __future__ import annotations

from typing import Any, Dict

from app.services import eda_service


def get_error_log(dataset_id: str) -> Dict[str, Any]:
    record = eda_service.get_dataset(dataset_id)
    snapshot = record.get("error_snapshot") or {"error_count": 0, "log_path": None, "errors": []}

    return {
        "dataset_id": dataset_id,
        "error_count": snapshot.get("error_count", 0),
        "log_path": snapshot.get("log_path"),
        "erreurs": snapshot.get("errors", []),
    }
