"""
Étape S6 — Notebooks standalone + LaTeX par algorithme dans MinIO.

Pour chaque algorithme en MySQL :
  1. Génère un notebook standalone qui exécute uniquement cet algo (2 graphiques)
  2. Génère un fichier LaTeX expliquant le concept mathématique (via LLM)
  3. Uploade vers MinIO :
       catalogue/{type_id}/problems/{prob_key}/algorithms/{algo_key}/notebook.ipynb
       catalogue/{type_id}/problems/{prob_key}/algorithms/{algo_key}/concept.tex
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("auto2.s6_algo_notebooks")

_TMP = Path(tempfile.gettempdir()) / "mobility_pipeline" / "algo_notebooks"


def run(
    type_ids: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, int]:
    """
    Génère notebooks standalone + LaTeX pour chaque algorithme.

    Retourne {"ok": N, "skip": N, "error": N}
    """
    from shared.db.repository import (
        CatalogueRepository, ProblemRepository,
        AlgorithmRepository, DatasetRepository,
    )
    from shared.storage.catalogue_storage import (
        upload_algorithm_notebook_standalone,
        upload_algorithm_concept_latex,
    )
    from shared.llm.router import chat_with_meta
    from automatisation_2.notebook_builder import (
        build_algorithm_standalone_notebook, save_notebook,
    )
    from automatisation_2.prompts.algo_concept_prompt import build_prompt as build_latex_prompt

    import nbformat

    cat_repo     = CatalogueRepository()
    prob_repo    = ProblemRepository()
    algo_repo    = AlgorithmRepository()
    dataset_repo = DatasetRepository()

    types = cat_repo.list_data_types()
    if type_ids:
        types = [t for t in types if t["id"] in type_ids]

    stats = {"ok": 0, "skip": 0, "error": 0}
    nb_counter = 1

    for dt in types:
        type_id   = dt["id"]
        type_name = dt["name"]
        problems  = prob_repo.find_problems(type_id)

        for prob in problems:
            prob_id  = prob.get("id")
            prob_key = prob.get("problem_key", f"prob_{prob_id}")
            prob_title = prob.get("title", prob_key)

            algos = algo_repo.find_algorithms(prob_id)
            if not algos:
                continue

            dataset_key = f"{type_id}_{prob_key}"
            dataset     = dataset_repo.find_dataset(type_id, dataset_key)
            csv_path    = dataset.get("file_path", "") if dataset else ""

            figures_dir = str(_TMP / "figures" / type_id / prob_key)
            Path(figures_dir).mkdir(parents=True, exist_ok=True)

            for algo in algos:
                algo_id   = algo.get("id", 0)
                algo_key  = algo.get("algorithm_key", f"algo_{algo_id}")
                algo_name = algo.get("name", algo_key)
                algo_cat  = algo.get("category", "")
                algo_desc = algo.get("description", "")
                skeleton  = algo.get("skeleton", "") or ""

                # ── Vérifier si déjà uploadé (skip si pas force) ─────────────
                if not force:
                    try:
                        from shared.storage.client import get_storage, BUCKET_CATALOGUE
                        from shared.storage.catalogue_storage import catalogue_path
                        nb_key = catalogue_path(type_id, "problems", prob_key,
                                                "algorithms", algo_key, "notebook.ipynb")
                        if get_storage().exists(BUCKET_CATALOGUE, nb_key):
                            log.info("[S6] %s/%s/%s — déjà dans MinIO, skip",
                                     type_name, prob_key, algo_key)
                            stats["skip"] += 1
                            continue
                    except Exception:
                        pass

                log.info("[S6] %s/%s/%s — génération notebook + LaTeX",
                         type_name, prob_key, algo_name[:40])

                # ── 1. Notebook standalone ────────────────────────────────────
                nb_path = _TMP / type_id / prob_key / f"{algo_key}.ipynb"
                try:
                    nb = build_algorithm_standalone_notebook(
                        algo_name    = algo_name,
                        algo_key     = algo_key,
                        algo_id      = algo_id,
                        skeleton_code= skeleton,
                        prob_title   = prob_title,
                        prob_key     = prob_key,
                        type_name    = type_name,
                        type_id      = type_id,
                        dataset_path = csv_path,
                        figures_dir  = figures_dir,
                        notebook_num = nb_counter,
                    )
                    nb_bytes = nbformat.writes(nb).encode("utf-8")
                    r_nb = upload_algorithm_notebook_standalone(
                        type_id, prob_key, algo_key, nb_bytes)
                    if r_nb.success:
                        log.info("[S6] ✓ notebook uploadé : %s", r_nb.object_key)
                    else:
                        log.warning("[S6] ✗ notebook upload échoué : %s", r_nb.error)
                except Exception as exc:
                    log.warning("[S6] %s/%s/%s — notebook erreur : %s",
                                type_name, prob_key, algo_key, exc)
                    stats["error"] += 1
                    continue
                finally:
                    nb_path.unlink(missing_ok=True)

                # ── 2. LaTeX concept mathématique ─────────────────────────────
                try:
                    sys_p, usr_p = build_latex_prompt(
                        algo_name  = algo_name,
                        category   = algo_cat,
                        description= algo_desc,
                        prob_title = prob_title,
                        type_name  = type_name,
                    )
                    llm_res = chat_with_meta(
                        messages=[
                            {"role": "system", "content": sys_p},
                            {"role": "user",   "content": usr_p},
                        ],
                        max_tokens  = 2000,
                        temperature = 0.2,
                        profile     = "text",
                    )
                    tex_content = ""
                    if llm_res.get("success") and llm_res.get("content"):
                        tex_content = llm_res["content"].strip()

                    if not tex_content:
                        tex_content = _fallback_latex(algo_name, algo_desc, prob_title, type_name)

                    r_tex = upload_algorithm_concept_latex(
                        type_id, prob_key, algo_key, tex_content)
                    if r_tex.success:
                        log.info("[S6] ✓ LaTeX uploadé : %s", r_tex.object_key)
                    else:
                        log.warning("[S6] ✗ LaTeX upload échoué : %s", r_tex.error)
                except Exception as exc:
                    log.warning("[S6] %s/%s/%s — LaTeX erreur : %s",
                                type_name, prob_key, algo_key, exc)

                stats["ok"] += 1
                nb_counter += 1

    log.info("[S6] Terminé — ok=%d skip=%d error=%d",
             stats["ok"], stats["skip"], stats["error"])
    return stats


def _fallback_latex(algo_name: str, description: str, prob_title: str, type_name: str) -> str:
    """LaTeX minimal si le LLM échoue."""
    desc_escaped = description.replace("_", "\\_").replace("&", "\\&").replace("%", "\\%")
    prob_escaped = prob_title.replace("_", "\\_").replace("&", "\\&").replace("%", "\\%")
    name_escaped = algo_name.replace("_", "\\_").replace("&", "\\&").replace("%", "\\%")
    type_escaped = type_name.replace("_", "\\_").replace("&", "\\&").replace("%", "\\%")
    return rf"""\documentclass{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage{{amsmath,amssymb,geometry}}
\geometry{{margin=2cm}}
\title{{Concept mathématique — {name_escaped}}}
\date{{}}
\begin{{document}}
\maketitle

\section{{Description}}
{desc_escaped}

\section{{Application}}
Algorithme appliqué au problème : \textit{{{prob_escaped}}}
dans le domaine : \textit{{{type_escaped}}}.

\end{{document}}
"""
