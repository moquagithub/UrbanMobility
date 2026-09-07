"""
Runner de l'Automatisation 1.

Orchestre les 3 étapes dans l'ordre :
  S1 → Lecture catalogue Excel → MySQL
  S2 → Génération problèmes via LLM → MySQL
  S3 → Génération algorithmes via LLM → MySQL

Usage depuis Python :
    from automatisation_1.runner import run
    result = run(catalogue_path=Path("data/catalogue.xlsx"))

Usage CLI : voir cli.py
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from shared.db.repository import CatalogueRepository, RunRepository
from shared.models import RunResult, StepResult
from automatisation_1.steps import s1_catalogue, s1b_enrich_types, s2_problems, s3_algorithms, s4_datasets
from automatisation_1.type_resolver import resolve_type_filter, AmbiguousTypeFilter

log = logging.getLogger("auto1.runner")


def run(
    catalogue_path: Path,
    type_filter: Optional[str] = None,
    force: bool = False,
    skip_s1: bool = False,
    skip_s1b: bool = False,
    skip_s2: bool = False,
    skip_s3: bool = False,
    skip_s4: bool = False,
    enrich: bool = False,
    max_problems: int = 12,
    max_algos: int = 9,
    patience: int = 2,
    on_progress=None,
) -> RunResult:
    """
    Exécute l'Automatisation 1 complète.

    Args:
        catalogue_path : chemin vers le fichier Excel du catalogue
        type_filter    : traite uniquement ce type (id ou nom partiel)
        force          : régénère même si les données existent en cache
        skip_s1/s2/s3  : saute une étape (utile pour les reprises partielles)
        enrich         : itère pour trouver plus de problèmes/algos jusqu'à saturation
        max_problems   : (enrich) nb max de problèmes à atteindre par type
        max_algos      : (enrich) nb max d'algorithmes à atteindre par problème
        patience       : (enrich) rounds sans nouveau résultat avant d'arrêter
        on_progress    : callback(step: str, message: str) pour suivi temps réel

    Retourne un RunResult avec tous les résultats.
    """
    started = datetime.now()
    cat_repo = CatalogueRepository()
    log_repo = RunRepository()

    def _progress(step: str, msg: str):
        log.info("[RUNNER] [%s] %s", step, msg)
        if on_progress:
            on_progress(step, msg)

    _progress("start", f"Démarrage Automatisation 1 — {catalogue_path.name}")

    # Résout --type une fois pour toutes en tête de run : si le filtre est
    # ambigu (sous-chaîne correspondant à plusieurs types), on arrête tout de
    # suite plutôt que de traiter silencieusement plusieurs types à la fois.
    if type_filter and cat_repo.is_available():
        try:
            resolve_type_filter(cat_repo.list_data_types(), type_filter)
        except AmbiguousTypeFilter as exc:
            msg = str(exc)
            _progress("start", f"ÉCHEC — {msg}")
            result = RunResult(data_type_id=type_filter, data_type_name=type_filter)
            result.steps.append(StepResult(step="runner", success=False, message=msg, errors=[msg]))
            return _finalize(result, None, log_repo, started, success=False)

    # Crée un run en base
    run_id = log_repo.create_run(
        project_id   = catalogue_path.stem,
        automation   = "auto1",
        data_type_id = type_filter,
    )

    result = RunResult(
        data_type_id   = type_filter or "all",
        data_type_name = type_filter or "Tous les types",
    )

    # ════════════════════════════════════════════════════════════════════════
    # STEP 1 — Catalogue Excel → MySQL
    # ════════════════════════════════════════════════════════════════════════
    if not skip_s1:
        _progress("S1", "Lecture du catalogue Excel…")
        t0 = time.time()
        s1_result = s1_catalogue.run(catalogue_path, force=force)
        s1_result.data["duration_s"] = round(time.time() - t0, 2)
        result.steps.append(s1_result)

        if not s1_result.success:
            _progress("S1", f"ÉCHEC — {s1_result.errors}")
            if run_id:
                log_repo.fail_run(run_id, str(s1_result.errors))
            return _finalize(result, run_id, log_repo, started, success=False)

        _progress("S1", s1_result.message)
    else:
        log.info("[RUNNER] S1 ignorée (skip_s1=True)")

    # ════════════════════════════════════════════════════════════════════════
    # STEP 1b — Enrichissement LLM des data_types
    # ════════════════════════════════════════════════════════════════════════
    if not skip_s1b:
        # Récupère les types bruts depuis MySQL pour l'enrichissement
        if cat_repo.is_available():
            _types_for_enrich = resolve_type_filter(cat_repo.list_data_types(), type_filter)
            if _types_for_enrich:
                _progress("S1b", f"Enrichissement de {len(_types_for_enrich)} type(s)…")
                t0 = time.time()
                s1b_result = s1b_enrich_types.run(
                    _types_for_enrich, force=force, type_filter=type_filter
                )
                s1b_result.data["duration_s"] = round(time.time() - t0, 2)
                result.steps.append(s1b_result)
                _progress("S1b", s1b_result.message)
    else:
        log.info("[RUNNER] S1b ignorée (skip_s1b=True)")

    # ── Récupère les types de données depuis MySQL (ou depuis S1) ─────────
    if cat_repo.is_available():
        all_types = resolve_type_filter(cat_repo.list_data_types(), type_filter)
    else:
        # Fallback : utilise les données retournées par S1
        s1 = next((s for s in result.steps if s.step == "s1_catalogue"), None)
        all_types = (s1.data.get("data_types", []) if s1 else [])

    if not all_types:
        msg = "Aucun type de données disponible pour les étapes suivantes"
        result.steps.append(StepResult(step="runner", success=False, message=msg))
        if run_id:
            log_repo.fail_run(run_id, msg)
        return _finalize(result, run_id, log_repo, started, success=False)

    _progress("runner", f"{len(all_types)} type(s) à traiter")

    # ════════════════════════════════════════════════════════════════════════
    # STEP 2 — LLM → Problèmes → MySQL
    # ════════════════════════════════════════════════════════════════════════
    if not skip_s2:
        _progress("S2", f"Génération des problèmes pour {len(all_types)} type(s)…")
        t0 = time.time()
        s2_result = s2_problems.run(
            all_types, force=force, type_filter=type_filter,
            enrich=enrich, max_per_type=max_problems, patience=patience,
        )
        s2_result.data["duration_s"] = round(time.time() - t0, 2)
        result.steps.append(s2_result)
        result.nb_problems = s2_result.data.get("total_saved", 0)

        if not s2_result.success and not s2_result.data.get("total_saved", 0):
            _progress("S2", f"ÉCHEC — {s2_result.errors}")
            if run_id:
                log_repo.fail_run(run_id, str(s2_result.errors))
            return _finalize(result, run_id, log_repo, started, success=False)

        _progress("S2", s2_result.message)
    else:
        log.info("[RUNNER] S2 ignorée (skip_s2=True)")

    # ════════════════════════════════════════════════════════════════════════
    # STEP 3 — LLM → Algorithmes → MySQL
    # ════════════════════════════════════════════════════════════════════════
    if not skip_s3:
        _progress("S3", f"Génération des algorithmes pour {len(all_types)} type(s)…")
        t0 = time.time()
        s3_result = s3_algorithms.run(
            all_types, force=force, type_filter=type_filter,
            enrich=enrich, max_per_problem=max_algos, patience=patience,
        )
        s3_result.data["duration_s"] = round(time.time() - t0, 2)
        result.steps.append(s3_result)
        result.nb_algorithms = s3_result.data.get("total_saved", 0)

        if not s3_result.success and not s3_result.data.get("total_saved", 0):
            _progress("S3", f"ÉCHEC — {s3_result.errors}")
            if run_id:
                log_repo.fail_run(run_id, str(s3_result.errors))
            return _finalize(result, run_id, log_repo, started, success=False)

        _progress("S3", s3_result.message)
    else:
        log.info("[RUNNER] S3 ignorée (skip_s3=True)")

    # ════════════════════════════════════════════════════════════════════════
    # STEP 4 — Datasets synthétiques → MySQL + CSV
    # ════════════════════════════════════════════════════════════════════════
    if not skip_s4:
        _progress("S4", f"Génération des datasets pour {len(all_types)} type(s)…")
        t0 = time.time()
        s4_result = s4_datasets.run(all_types, force=force, type_filter=type_filter)
        s4_result.data["duration_s"] = round(time.time() - t0, 2)
        result.steps.append(s4_result)
        _progress("S4", s4_result.message)
    else:
        log.info("[RUNNER] S4 ignorée (skip_s4=True)")

    _progress("done",
              f"✓ Automatisation 1 terminée — "
              f"{result.nb_problems} problème(s), "
              f"{result.nb_algorithms} algorithme(s)")

    return _finalize(result, run_id, log_repo, started, success=True)


def _finalize(
    result: RunResult,
    run_id,
    log_repo: RunRepository,
    started: datetime,
    success: bool,
) -> RunResult:
    result.success = success
    result.completed_at = datetime.now()

    if run_id:
        if success:
            log_repo.complete_run(run_id, {
                "total_problems":   result.nb_problems,
                "total_algorithms": result.nb_algorithms,
                "duration_s":       result.duration_seconds,
            })
        else:
            log_repo.fail_run(run_id)

    return result
