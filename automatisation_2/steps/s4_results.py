"""
Étape S4 — Agrégation des résultats et mise à jour des statuts.

Après l'exécution des notebooks (S3), cette étape :
1. Collecte les métriques de tous les notebooks exécutés par type
2. Calcule les statistiques agrégées (F1 moyen, taux de succès...)
3. Met à jour processing_status → 'notebooks_done' dans data_types
4. Génère un rapport JSON de synthèse par type de données
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from shared.db.connection import get_connection
from shared.db.repository import CatalogueRepository, NotebookRepository

log = logging.getLogger("auto2.s4_results")

REPORTS_DIR = Path(tempfile.gettempdir()) / "mobility_pipeline" / "reports"


def run(
    type_ids: Optional[List[str]] = None,
) -> Dict[str, int]:
    """
    Agrège les résultats et marque les types comme 'notebooks_done'.

    Args:
        type_ids : restreindre à certains types (None = tous)

    Retourne {"done": N, "partial": N, "skip": N}
    """
    cat_repo      = CatalogueRepository()
    notebook_repo = NotebookRepository()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    types = cat_repo.list_data_types()
    if type_ids:
        types = [t for t in types if t["id"] in type_ids]

    stats = {"done": 0, "partial": 0, "skip": 0}

    for dt in types:
        type_id   = dt["id"]
        type_name = dt["name"]

        all_notebooks = notebook_repo.list_for_type(type_id)
        if not all_notebooks:
            log.info("[S4] %s — aucun notebook, skip", type_name)
            stats["skip"] += 1
            continue

        total     = len(all_notebooks)
        executed  = sum(1 for n in all_notebooks if n.get("status") == "executed")
        errors    = sum(1 for n in all_notebooks if n.get("status") in ("error", "timeout"))
        generated = sum(1 for n in all_notebooks if n.get("status") == "generated")

        log.info(
            "[S4] %s — total=%d exécutés=%d erreurs=%d en_attente=%d",
            type_name, total, executed, errors, generated,
        )

        if generated > 0:
            log.info("[S4] %s — %d notebooks encore en attente, partial", type_name, generated)
            stats["partial"] += 1
            continue

        # Collecter les métriques
        results = notebook_repo.get_results_for_type(type_id)
        agg = _aggregate_metrics(results)

        # Rapport JSON
        report = {
            "type_id":        type_id,
            "type_name":      type_name,
            "total_notebooks": total,
            "executed":       executed,
            "errors":         errors,
            "success_rate":   round(executed / total, 4) if total > 0 else 0.0,
            "metrics_summary": agg,
            "notebooks": [
                {
                    "notebook_key":   n.get("notebook_key", ""),
                    "problem_title":  n.get("problem_title", ""),
                    "algorithm_name": n.get("algorithm_name", ""),
                    "status":         n.get("status", ""),
                    "execution_time_sec": n.get("execution_time_sec"),
                }
                for n in all_notebooks
            ],
        }

        report_path = REPORTS_DIR / f"{type_id}_report.json"
        try:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            log.info("[S4] Rapport JSON temporaire : %s", report_path)
        finally:
            report_path.unlink(missing_ok=True)

        # Mise à jour processing_status
        if executed > 0:
            _update_status(type_id, "notebooks_done")
            log.info("[S4] %s → notebooks_done", type_name)
            stats["done"] += 1
        else:
            stats["partial"] += 1

    log.info("[S4] Terminé — done=%d partial=%d skip=%d", stats["done"], stats["partial"], stats["skip"])
    return stats


def _aggregate_metrics(results: List[Dict]) -> Dict:
    """Calcule les métriques agrégées sur l'ensemble des résultats."""
    if not results:
        return {}

    f1_scores    = []
    precisions   = []
    recalls      = []
    exec_times   = []

    for r in results:
        metrics = r.get("final_metrics_json") or r.get("metrics_json") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        if "f1" in metrics:
            f1_scores.append(float(metrics["f1"]))
        if "precision" in metrics:
            precisions.append(float(metrics["precision"]))
        if "recall" in metrics:
            recalls.append(float(metrics["recall"]))

        exec_time = r.get("execution_time_sec")
        if exec_time is not None:
            exec_times.append(float(exec_time))

    def _avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else None

    return {
        "f1_mean":         _avg(f1_scores),
        "precision_mean":  _avg(precisions),
        "recall_mean":     _avg(recalls),
        "exec_time_mean":  _avg(exec_times),
        "n_with_metrics":  len(f1_scores),
        "n_results":       len(results),
    }


def _update_status(type_id: str, status: str) -> None:
    """Met à jour processing_status dans data_types."""
    conn = get_connection()
    if not conn:
        log.warning("[S4] DB non disponible pour mise à jour statut")
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE data_types SET processing_status=%s, updated_at=NOW() WHERE id=%s",
                (status, type_id),
            )
        conn.commit()
    except Exception as exc:
        log.warning("[S4] Erreur update_status : %s", exc)
    finally:
        conn.close()
