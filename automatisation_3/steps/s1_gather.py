"""
Étape S1 — Collecte des données MySQL par PROBLÈME pour chaque type de données.

Nouvelle architecture (par problème) :
  • Retourne une liste d'items, un par (type × problème)
  • Chaque item contient les algorithmes + métriques d'exécution de CE problème
  • Un rapport PDF sera généré par item (par problème)

Retour :
    [
        {
            "data_type":           {...},         # type de données
            "problem":             {...},         # LE problème de cet item
            "chapters":            [{algorithm, exec_metrics, exec_time, figures}, ...],
            "metrics_summary":     {...},
            "comparison_figure":   "path/or/minio/key",  # figure comparaison si dispo
        },
        ...  # un item par (type, problème)
    ]
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from shared.db.repository import (
    CatalogueRepository,
    ProblemRepository,
    AlgorithmRepository,
    NotebookRepository,
)

log = logging.getLogger("auto3.s1_gather")


def run(type_ids: Optional[List[str]] = None) -> List[Dict]:
    """
    Collecte toutes les données nécessaires pour générer les rapports par problème.

    Args:
        type_ids : restreindre à certains type_id (None = tous avec statut éligible)

    Retourne une liste d'items, un par (type × problème).
    """
    cat_repo      = CatalogueRepository()
    prob_repo     = ProblemRepository()
    algo_repo     = AlgorithmRepository()
    notebook_repo = NotebookRepository()

    if type_ids:
        all_types = cat_repo.list_data_types()
        types = [t for t in all_types if t["id"] in type_ids]
    else:
        all_types = cat_repo.list_data_types()
        types = [
            t for t in all_types
            if t.get("processing_status") in ("algorithms_done", "notebooks_done", "report_done")
        ]

    if not types:
        log.warning("[S1] Aucun type éligible pour la génération de rapports.")
        return []

    log.info("[S1] %d type(s) à collecter (mode par-problème).", len(types))

    result = []
    for dt in types:
        type_id   = dt["id"]
        type_name = dt["name"]

        problems   = prob_repo.find_problems(type_id) or []
        algorithms = algo_repo.find_all_for_data_type(type_id) or []

        if not algorithms:
            log.warning("[S1] %s — aucun algorithme, skip.", type_name)
            continue

        _deserialize_algorithms(algorithms)
        nb_results = notebook_repo.get_results_for_type(type_id)
        best_results = _build_best_results_index(nb_results)

        for problem in problems:
            prob_id  = problem["id"]
            prob_key = problem.get("problem_key", f"prob_{prob_id}")

            # Algorithmes de ce problème uniquement
            prob_algos = [a for a in algorithms if a.get("problem_id") == prob_id]
            if not prob_algos:
                log.debug("[S1] %s/%s — aucun algorithme, skip.", type_name, prob_key)
                continue

            # Construire les chapitres (1 par algo)
            chapters = []
            for algo in prob_algos:
                algo_id = algo["id"]
                best = best_results.get(algo_id, {})

                exec_metrics = best.get("final_metrics_json") or best.get("metrics_json") or {}
                if isinstance(exec_metrics, str):
                    try:
                        exec_metrics = json.loads(exec_metrics)
                    except Exception:
                        exec_metrics = {}

                output_figures = best.get("output_figures") or []
                if isinstance(output_figures, str):
                    try:
                        output_figures = json.loads(output_figures)
                    except Exception:
                        output_figures = []

                chapters.append({
                    "algorithm":    algo,
                    "problem":      problem,  # gardé pour compatibilité latex_builder
                    "exec_metrics": exec_metrics,
                    "exec_time":    best.get("execution_time_sec"),
                    "figures":      output_figures[:4],
                })

            # Figure de comparaison pour ce problème
            comparison_figure = _find_comparison_figure(type_id, prob_key)

            result.append({
                "data_type":        dt,
                "problem":          problem,
                "chapters":         chapters,
                "metrics_summary":  _aggregate(chapters),
                "comparison_figure": comparison_figure,
            })
            log.info("[S1] %s/%s — %d algorithme(s) collectés.", type_name, prob_key, len(chapters))

    log.info("[S1] Total : %d item(s) (type × problème) prêts pour les rapports.", len(result))
    return result


def _find_comparison_figure(type_id: str, prob_key: str) -> Dict[str, str]:
    """
    Cherche les figures de comparaison (performance + timing) d'un problème.

    Retourne des références qui peuvent être soit un chemin disque local,
    soit une clé Minio du bucket `catalogue` — à résoudre par le caller
    (voir latex_builder.write_report_files / _resolve_figure_ref).
    """
    from pathlib import Path
    from shared.storage.catalogue_storage import catalogue_path
    from shared.storage.client import get_storage, BUCKET_CATALOGUE

    refs: Dict[str, str] = {}

    # 1) Chemin disque local (cas où S5 vient de tourner dans le même process)
    local_candidates = {
        "performance": [
            Path("data/figures") / type_id / prob_key / "comparison.png",
            Path("data/figures") / type_id / f"compare_{prob_key}.png",
            Path("data/figures") / type_id / f"compare_{prob_key}_performance.png",
        ],
        "timing": [
            Path("data/figures") / type_id / f"compare_{prob_key}_timing.png",
        ],
    }
    for suffix, candidates in local_candidates.items():
        for c in candidates:
            if c.exists():
                refs[suffix] = str(c)
                break

    # 2) Clé déterministe dans le bucket catalogue (cas normal — S5 a uploadé puis nettoyé le local)
    store = get_storage()
    for suffix in ("performance", "timing"):
        if suffix in refs:
            continue
        key = catalogue_path(type_id, "problems", prob_key, f"comparison_{suffix}.png")
        if store.exists(BUCKET_CATALOGUE, key):
            refs[suffix] = key

    # 3) Fallback DB (ancien schéma ne gardant qu'une seule clé, sans suffixe garanti)
    if "performance" not in refs:
        try:
            from shared.db.repository.figure_repo import FigureRepository
            fig_repo = FigureRepository()
            figures = fig_repo.find_figures(type_id)
            for f in figures:
                if prob_key in f.get("figure_key", "") and f.get("minio_key"):
                    refs["performance"] = f["minio_key"]
                    break
        except Exception:
            pass

    return refs


def _deserialize_algorithms(algorithms: List[Dict]) -> None:
    json_fields = [
        "hyperparameters", "required_libraries", "evaluation_metrics",
        "input_format", "output_format", "references",
        "advantages", "limitations",
    ]
    for algo in algorithms:
        for field in json_fields:
            val = algo.get(field)
            if isinstance(val, str) and val:
                try:
                    algo[field] = json.loads(val)
                except Exception:
                    algo[field] = []


def _build_best_results_index(nb_results: List[Dict]) -> Dict[int, Dict]:
    """Pour chaque algorithm_id, garde le résultat avec le meilleur F1."""
    index: Dict[int, Dict] = {}
    for r in nb_results:
        algo_id = r.get("algorithm_id")
        if algo_id is None:
            continue
        metrics = r.get("final_metrics_json") or r.get("metrics_json") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        f1_new = metrics.get("f1", 0.0) or 0.0
        existing = index.get(algo_id)
        if existing is None:
            index[algo_id] = r
        else:
            ex_m = existing.get("final_metrics_json") or existing.get("metrics_json") or {}
            if isinstance(ex_m, str):
                try:
                    ex_m = json.loads(ex_m)
                except Exception:
                    ex_m = {}
            if f1_new > (ex_m.get("f1", 0.0) or 0.0):
                index[algo_id] = r
    return index


def _aggregate(chapters: List[Dict]) -> Dict:
    f1s, precs, recs = [], [], []
    for ch in chapters:
        m = ch.get("exec_metrics") or {}
        if m.get("f1")        is not None: f1s.append(float(m["f1"]))
        if m.get("precision") is not None: precs.append(float(m["precision"]))
        if m.get("recall")    is not None: recs.append(float(m["recall"]))

    def _avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else None

    return {
        "f1_mean":        _avg(f1s),
        "precision_mean": _avg(precs),
        "recall_mean":    _avg(recs),
        "n_algorithms":   len(chapters),
        "n_with_metrics": len(f1s),
    }
