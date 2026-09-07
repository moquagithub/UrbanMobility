"""
Runner de l'Automatisation 3 — LaTeX → PDF.

  S1 : Collecte des données MySQL (types + problèmes + algorithmes + métriques)
  S2 : Génération des fichiers .tex (main + chapitres + annexes)
  S3 : Compilation pdflatex (3 passes + auto-réparation LLM ×3)
  S4 : Stockage PDF en MySQL + processing_status → report_done
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("auto3.runner")


def run_pipeline(
    type_ids: Optional[List[str]] = None,
    force: bool = False,
    skip_s1: bool = False,
    skip_s2: bool = False,
    skip_s3: bool = False,
    skip_s4: bool = False,
    use_llm_repair: bool = True,
    max_repair_rounds: int = 3,
) -> Dict:
    """
    Exécute le pipeline complet de l'Automatisation 3.

    Args:
        type_ids          : restreindre à certains type_id (None = tous éligibles)
        force             : forcer la régénération même si PDF déjà existant
        skip_s1/s2/s3/s4  : sauter une étape
        use_llm_repair    : activer la réparation LaTeX par LLM
        max_repair_rounds : tentatives de réparation max

    Retourne un dict résumant les résultats.
    """
    t_start = time.perf_counter()
    results = {}

    log.info("=" * 60)
    log.info("AUTOMATISATION 3 — RAPPORTS PDF")
    log.info("type_ids=%s  force=%s", type_ids or "tous", force)
    log.info("=" * 60)

    # ── S1 : Collecte MySQL ───────────────────────────────────────────────
    gathered = []
    if not skip_s1:
        log.info("── S1 Collecte ──────────────────────────────────────────")
        from automatisation_3.steps.s1_gather import run as s1_run
        gathered = s1_run(type_ids=type_ids)
        results["s1_gather"] = {"types": len(gathered)}
        log.info("S1 terminé : %d type(s) collectés.", len(gathered))
    else:
        log.info("S1 ignoré (--skip-s1)")
        results["s1_gather"] = "skipped"

    if not gathered:
        log.warning("Aucune donnée collectée — pipeline arrêté.")
        return results

    # ── S2 : Génération LaTeX ─────────────────────────────────────────────
    # Même si S2 est sauté, on injecte tex_dir depuis le disque si disponible
    reports_base = Path("data/reports")
    for item in gathered:
        if not item.get("tex_dir"):
            tid      = item["data_type"]["id"]
            problem  = item.get("problem", {})
            prob_key = problem.get("problem_key", f"prob_{problem.get('id', 'x')}")
            # Nouveau chemin par problème
            candidate = reports_base / tid / prob_key
            if not (candidate / "main.tex").exists():
                # Fallback ancien chemin (migration)
                candidate = reports_base / tid
            if (candidate / "main.tex").exists():
                item["tex_dir"]    = str(candidate)
                item["n_chapters"] = len(item.get("chapters", []))

    latex_items = gathered
    if not skip_s2:
        log.info("── S2 LaTeX ─────────────────────────────────────────────")
        from automatisation_3.steps.s2_latex import run as s2_run
        latex_items = s2_run(gathered, force=force)
        n_ok = sum(1 for x in latex_items if x.get("n_chapters", 0) > 0)
        results["s2_latex"] = {"ok": n_ok, "total": len(latex_items)}
        log.info("S2 terminé : %d/%d types avec LaTeX généré.", n_ok, len(latex_items))
    else:
        log.info("S2 ignoré (--skip-s2)")
        results["s2_latex"] = "skipped"

    # ── S3 : Compilation PDF ──────────────────────────────────────────────
    compiled_items = latex_items
    if not skip_s3:
        log.info("── S3 Compilation ───────────────────────────────────────")
        from automatisation_3.steps.s3_compile import run as s3_run
        compiled_items = s3_run(
            latex_items,
            max_repair_rounds=max_repair_rounds,
            use_llm_repair=use_llm_repair,
        )
        n_ok    = sum(1 for x in compiled_items if x.get("compile_success"))
        n_error = sum(1 for x in compiled_items if not x.get("compile_success"))
        results["s3_compile"] = {"ok": n_ok, "error": n_error}
        log.info("S3 terminé : %d PDF générés, %d erreurs.", n_ok, n_error)
    else:
        log.info("S3 ignoré (--skip-s3)")
        results["s3_compile"] = "skipped"

    # ── S4 : Stockage MySQL ───────────────────────────────────────────────
    if not skip_s4:
        log.info("── S4 Stockage ──────────────────────────────────────────")
        from automatisation_3.steps.s4_store import run as s4_run
        s4_result = s4_run(compiled_items)
        results["s4_store"] = s4_result
        log.info("S4 terminé : %s", s4_result)
    else:
        log.info("S4 ignoré (--skip-s4)")
        results["s4_store"] = "skipped"

    elapsed = round(time.perf_counter() - t_start, 1)
    results["elapsed_sec"] = elapsed

    log.info("=" * 60)
    log.info("AUTOMATISATION 3 — TERMINÉE en %.1fs", elapsed)
    log.info("=" * 60)

    return results
