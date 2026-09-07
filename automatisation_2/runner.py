"""
Runner principal de l'Automatisation 2.

Orchestre les 4 étapes du pipeline :
  S1 → Génération datasets (LLM schema → CSV + MySQL)
  S2 → Construction notebooks (.ipynb depuis données DB)
  S3 → Exécution notebooks (nbconvert ExecutePreprocessor)
  S4 → Agrégation résultats + statuts notebooks_done

Les deux automatisations partagent la même base MySQL.
Auto1 remplit data_types / problems / algorithms.
Auto2 lit ces tables et produit datasets / notebooks / notebook_results.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=True)
except ImportError:
    pass

log = logging.getLogger("auto2.runner")


def run_pipeline(
    type_ids: Optional[List[str]] = None,
    force: bool = False,
    skip_s1: bool = False,
    skip_s2: bool = False,
    skip_s3: bool = False,
    skip_s4: bool = False,
    skip_s5: bool = False,
    skip_s6: bool = False,
    max_notebooks: Optional[int] = None,
    run_id: Optional[int] = None,
) -> Dict:
    """
    Exécute le pipeline complet de l'Automatisation 2.

    Args:
        type_ids      : restreindre à certains data_type_id (None = tous)
        force         : forcer la régénération même si les données existent déjà
        skip_s1       : sauter la génération des datasets
        skip_s2       : sauter la construction des notebooks
        skip_s3       : sauter l'exécution des notebooks
        skip_s4       : sauter l'agrégation des résultats
        max_notebooks : limiter le nombre de notebooks exécutés en S3
        run_id        : ID de run auto1 associé (pour traçabilité)

    Retourne un dict résumant les résultats de chaque étape.
    """
    t_start  = time.perf_counter()
    results  = {}

    log.info("=" * 60)
    log.info("AUTOMATISATION 2 — DÉMARRAGE")
    log.info("type_ids=%s  force=%s", type_ids or "tous", force)
    log.info("=" * 60)

    # ── S1 : Génération des datasets ──────────────────────────────────────
    if not skip_s1:
        log.info("── S1 Datasets ──────────────────────────────────────────")
        from automatisation_2.steps.s1_datasets import run as s1_run
        s1_result = s1_run(type_ids=type_ids, force=force)
        results["s1_datasets"] = s1_result
        log.info("S1 terminé : %s", s1_result)
    else:
        log.info("S1 ignoré (--skip-s1)")
        results["s1_datasets"] = "skipped"

    # ── S2 : Construction des notebooks ───────────────────────────────────
    if not skip_s2:
        log.info("── S2 Notebooks ─────────────────────────────────────────")
        from automatisation_2.steps.s2_notebooks import run as s2_run
        s2_result = s2_run(type_ids=type_ids, force=force)
        results["s2_notebooks"] = s2_result
        log.info("S2 terminé : %s", s2_result)
    else:
        log.info("S2 ignoré (--skip-s2)")
        results["s2_notebooks"] = "skipped"

    # ── S3 : Exécution des notebooks ──────────────────────────────────────
    if not skip_s3:
        log.info("── S3 Execution ─────────────────────────────────────────")
        from automatisation_2.steps.s3_execution import run as s3_run
        s3_result = s3_run(
            type_ids=type_ids,
            run_id=run_id,
            max_notebooks=max_notebooks,
        )
        results["s3_execution"] = s3_result
        log.info("S3 terminé : %s", s3_result)
    else:
        log.info("S3 ignoré (--skip-s3)")
        results["s3_execution"] = "skipped"

    # ── S4 : Agrégation des résultats ─────────────────────────────────────
    if not skip_s4:
        log.info("── S4 Results ───────────────────────────────────────────")
        from automatisation_2.steps.s4_results import run as s4_run
        s4_result = s4_run(type_ids=type_ids)
        results["s4_results"] = s4_result
        log.info("S4 terminé : %s", s4_result)
    else:
        log.info("S4 ignoré (--skip-s4)")
        results["s4_results"] = "skipped"

    # ── S5 : Graphiques de comparaison inter-algorithmes ──────────────────
    if not skip_s5:
        log.info("── S5 Comparaison ───────────────────────────────────────")
        from automatisation_2.steps.s5_compare import run as s5_run
        s5_result = s5_run(type_ids=type_ids)
        results["s5_compare"] = s5_result
        log.info("S5 terminé : %s", s5_result)
    else:
        log.info("S5 ignoré (--skip-s5)")
        results["s5_compare"] = "skipped"

    # ── S6 : Notebooks standalone + LaTeX par algorithme ─────────────────
    if not skip_s6:
        log.info("── S6 Algo Notebooks ────────────────────────────────────")
        from automatisation_2.steps.s6_algo_notebooks import run as s6_run
        s6_result = s6_run(type_ids=type_ids, force=force)
        results["s6_algo_notebooks"] = s6_result
        log.info("S6 terminé : %s", s6_result)
    else:
        log.info("S6 ignoré (--skip-s6)")
        results["s6_algo_notebooks"] = "skipped"

    elapsed = round(time.perf_counter() - t_start, 1)
    results["elapsed_sec"] = elapsed

    log.info("=" * 60)
    log.info("AUTOMATISATION 2 — TERMINÉE en %.1fs", elapsed)
    log.info("=" * 60)

    return results
