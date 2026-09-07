"""
Étape S3 — Compilation LaTeX → PDF avec auto-réparation.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List

from automatisation_3.latex_compiler import compile_pdf

log = logging.getLogger("auto3.s3_compile")


def run(
    latex_items: List[Dict],
    max_repair_rounds: int = 3,
    use_llm_repair: bool = True,
) -> List[Dict]:
    """
    Compile les rapports LaTeX en PDF.

    Args:
        latex_items       : résultat de s2_latex.run() — chaque item a tex_dir
        max_repair_rounds : tentatives de réparation LLM si compilation échoue
        use_llm_repair    : activer la réparation automatique

    Retourne la liste enrichie avec 'pdf_path', 'compile_success', 'compile_errors'.
    """
    results = []

    for item in latex_items:
        dt        = item["data_type"]
        type_name = dt["name"]
        problem   = item.get("problem", {})
        prob_key  = problem.get("problem_key", "")
        label     = f"{type_name}/{prob_key}" if prob_key else type_name
        tex_dir   = item.get("tex_dir", "")
        n_chap    = item.get("n_chapters", 0)

        if not tex_dir or n_chap == 0:
            log.warning("[S3] %s — pas de répertoire LaTeX, skip.", label)
            item["compile_success"] = False
            item["compile_errors"]  = item.get("s2_error", "tex_dir manquant")
            item["pdf_path"]        = None
            item["compile_rounds"]  = 0
            results.append(item)
            continue

        tex_path = Path(tex_dir)
        if not (tex_path / "main.tex").exists():
            log.warning("[S3] %s — main.tex absent dans %s, skip.", label, tex_dir)
            item["compile_success"] = False
            item["compile_errors"]  = "main.tex absent"
            item["pdf_path"]        = None
            item["compile_rounds"]  = 0
            results.append(item)
            continue

        log.info("[S3] %s — compilation LaTeX (%d chapitres)...", label, n_chap)
        t0 = time.perf_counter()

        success, pdf_path, errors, rounds = compile_pdf(
            tex_dir=tex_path,
            max_repair_rounds=max_repair_rounds,
            use_llm_repair=use_llm_repair,
        )

        elapsed = round(time.perf_counter() - t0, 1)

        if success:
            log.info("[S3] %s — PDF généré en %.1fs (%d passes) : %s",
                     label, elapsed, rounds, pdf_path)
        else:
            log.warning("[S3] %s — compilation échouée en %.1fs (%d passes) : %s",
                        label, elapsed, rounds, errors[:200])

        item["compile_success"]     = success
        item["compile_errors"]      = errors[:2000]
        item["pdf_path"]            = str(pdf_path) if pdf_path else None
        item["compile_rounds"]      = rounds
        item["generation_time_sec"] = elapsed
        results.append(item)

    return results
