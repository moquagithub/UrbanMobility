"""
Étape S2 — Génération des fichiers LaTeX depuis les données collectées.

Architecture par problème : un répertoire LaTeX par (type × problème).
  data/reports/{type_id}/{prob_key}/main.tex
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from automatisation_3.latex_builder import write_report_files

log = logging.getLogger("auto3.s2_latex")

REPORTS_DIR = Path("data/reports")


def run(
    gathered: List[Dict],
    force: bool = False,
) -> List[Dict]:
    """
    Génère les fichiers .tex pour chaque (type × problème).

    Args:
        gathered : résultat de s1_gather.run() — un item par (type, problème)
        force    : régénère même si le répertoire existe

    Retourne la liste enrichie avec les champs 'tex_dir' et 'n_chapters' ajoutés.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for item in gathered:
        dt        = item["data_type"]
        problem   = item.get("problem", {})
        chapters  = item["chapters"]
        type_id   = dt["id"]
        type_name = dt["name"]
        prob_key  = problem.get("problem_key", f"prob_{problem.get('id', 'x')}")
        prob_title = problem.get("title", prob_key)

        report_dir = REPORTS_DIR / type_id / prob_key

        # Cache check
        if not force and (report_dir / "main.tex").exists():
            log.info("[S2] %s/%s — main.tex existant, skip.", type_name, prob_key)
            item["tex_dir"]    = str(report_dir)
            item["n_chapters"] = len(chapters)
            results.append(item)
            continue

        log.info("[S2] %s/%s — génération LaTeX (%d algos)...",
                 type_name, prob_key, len(chapters))

        try:
            created_files = write_report_files(
                report_dir        = report_dir,
                data_type         = dt,
                chapters_data     = chapters,
                problem           = problem,
                comparison_figure = item.get("comparison_figure") or {},
            )
            log.info("[S2] %s/%s — %d fichiers écrits.", type_name, prob_key, len(created_files))
            item["tex_dir"]    = str(report_dir)
            item["n_chapters"] = len(chapters)
            results.append(item)
        except Exception as exc:
            log.warning("[S2] %s/%s — erreur LaTeX builder : %s", type_name, prob_key, exc)
            item["tex_dir"]    = str(report_dir)
            item["n_chapters"] = 0
            item["s2_error"]   = str(exc)
            results.append(item)

    return results
