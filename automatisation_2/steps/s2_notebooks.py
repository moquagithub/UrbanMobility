"""
Étape S2 — Construction des notebooks de comparaison par problème.

Nouvelle architecture (par problème) :
  • 1 notebook par problème (au lieu de 1 par algorithme×problème)
  • Le notebook exécute TOUS les algorithmes du problème et compare leurs résultats
  • Clé notebook : {type_id}_{prob_key}_comparison
  • Upload vers bucket `catalogue` : {type_id}/problems/{prob_key}/notebook.ipynb
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from shared.db.repository import (
    CatalogueRepository,
    ProblemRepository,
    AlgorithmRepository,
    DatasetRepository,
    NotebookRepository,
)
from automatisation_2.notebook_builder import build_comparison_notebook, save_notebook

log = logging.getLogger("auto2.s2_notebooks")

NOTEBOOKS_DIR = Path(tempfile.gettempdir()) / "mobility_pipeline" / "notebooks"


def run(
    type_ids: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, int]:
    """
    Génère un notebook de comparaison par problème.

    Args:
        type_ids : liste de data_type_id à traiter (None = tous)
        force    : régénère même si le notebook existe déjà

    Retourne {"ok": N, "skip": N, "error": N}
    """
    cat_repo      = CatalogueRepository()
    prob_repo     = ProblemRepository()
    algo_repo     = AlgorithmRepository()
    dataset_repo  = DatasetRepository()
    notebook_repo = NotebookRepository()

    NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)

    types = cat_repo.list_data_types()
    if type_ids:
        types = [t for t in types if t["id"] in type_ids]

    stats     = {"ok": 0, "skip": 0, "error": 0}
    nb_counter = 0

    for dt in types:
        type_id   = dt["id"]
        type_name = dt["name"]

        problems = prob_repo.find_problems(type_id) or []
        if not problems:
            log.warning("[S2] %s — aucun problème, skip", type_name)
            stats["skip"] += 1
            continue

        all_algos = algo_repo.find_all_for_data_type(type_id) or []
        if not all_algos:
            log.warning("[S2] %s — aucun algorithme, skip", type_name)
            stats["skip"] += 1
            continue

        _deserialize_algorithms(all_algos)

        for prob in problems:
            prob_id    = prob["id"]
            prob_key   = prob.get("problem_key", f"prob_{prob_id}")
            prob_title = prob.get("title", "")

            notebook_key = f"{type_id}_{prob_key}_comparison"
            nb_counter  += 1

            # Algorithmes liés à ce problème
            prob_algos = [a for a in all_algos if a.get("problem_id") == prob_id]

            # Cache — régénère si le nombre d'algos a changé depuis la dernière génération
            # (ex. enrichissement S3 ayant ajouté des algos après la 1re génération du notebook)
            existing_nb = notebook_repo.find_by_key(notebook_key)
            if not force and existing_nb:
                if existing_nb.get("n_algorithms") == len(prob_algos):
                    log.info("[S2] Notebook comparaison à jour : %s (%d algos)",
                             notebook_key, len(prob_algos))
                    stats["skip"] += 1
                    continue
                log.info(
                    "[S2] Notebook comparaison obsolète : %s (%s → %d algos) — régénération",
                    notebook_key, existing_nb.get("n_algorithms"), len(prob_algos),
                )

            if not prob_algos:
                log.warning("[S2] %s/%s — aucun algorithme pour ce problème, skip",
                            type_name, prob_key)
                stats["skip"] += 1
                continue

            # Dataset de ce problème
            dataset_key = f"{type_id}_{prob_key}"
            dataset     = dataset_repo.find_dataset(type_id, dataset_key)
            csv_path    = dataset.get("file_path", "") if dataset else ""

            # Adapter les skeletons aux colonnes réelles du dataset avant de
            # construire le notebook — évite les KeyError à l'exécution
            if dataset:
                prob_algos = _adapt_skeletons_to_dataset(
                    prob_algos, dataset, prob, dt
                )

            figures_dir = str(Path(tempfile.gettempdir()) / "mobility_pipeline" / "figures" / type_id / prob_key)
            Path(figures_dir).mkdir(parents=True, exist_ok=True)

            log.info("[S2] Génération notebook comparaison : %s (%d algos)",
                     notebook_key, len(prob_algos))

            try:
                nb = build_comparison_notebook(
                    data_type   = dt,
                    problem     = prob,
                    algorithms  = prob_algos,
                    dataset_key = dataset_key,
                    dataset_path= csv_path,
                    figures_dir = figures_dir,
                    notebook_num= nb_counter,
                )
            except Exception as exc:
                log.warning("[S2] %s — build_comparison_notebook erreur : %s", notebook_key, exc)
                stats["error"] += 1
                continue

            # Sauvegarde disque
            nb_path = NOTEBOOKS_DIR / type_id / f"{notebook_key}.ipynb"
            try:
                saved_path = save_notebook(nb, str(nb_path))
            except Exception as exc:
                log.warning("[S2] %s — save_notebook erreur : %s", notebook_key, exc)
                stats["error"] += 1
                continue

            # Upload vers bucket catalogue (chemin hiérarchique)
            cat_minio_key = ""
            try:
                import nbformat as _nbf
                _nb_bytes = _nbf.writes(nb).encode("utf-8")
                from shared.storage.catalogue_storage import upload_notebook
                _r = upload_notebook(type_id, prob_key, _nb_bytes)
                if _r.success:
                    cat_minio_key = _r.object_key
                    log.debug("[S2] Catalogue Minio ↑ %s", cat_minio_key)
            except Exception as exc:
                log.debug("[S2] Catalogue Minio upload échoué : %s", exc)

            # Upload vers bucket notebooks (pour S3 — exécution)
            minio_key = ""
            try:
                from shared.storage import get_storage
                store = get_storage()
                result = store.upload_notebook(type_id, notebook_key, Path(saved_path))
                if result.success:
                    minio_key = result.object_key
            except Exception as exc:
                log.debug("[S2] Notebooks Minio upload échoué : %s", exc)

            title = f"Comparaison — {prob_title[:80]}"

            nb_id = notebook_repo.save_notebook(
                data_type_id = type_id,
                problem_id   = prob_id,
                notebook_key = notebook_key,
                title        = title,
                algorithm_id = None,  # notebook de comparaison (pas un seul algo)
                notebook_path= saved_path,
                minio_key    = minio_key or cat_minio_key,
                n_algorithms = len(prob_algos),
            )

            if nb_id:
                log.info("[S2] ✓ Notebook#%d comparaison sauvegardé : %s", nb_id, notebook_key)
                if minio_key:
                    try:
                        Path(saved_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                stats["ok"] += 1
            else:
                log.warning("[S2] %s — sauvegarde MySQL échouée", notebook_key)
                stats["error"] += 1

    log.info("[S2] Terminé — ok=%d skip=%d error=%d", stats["ok"], stats["skip"], stats["error"])
    return stats


def _adapt_skeletons_to_dataset(
    algorithms: List[Dict],
    dataset: Dict,
    problem: Dict,
    data_type: Dict,
) -> List[Dict]:
    """
    Pour chaque algorithme dont le skeleton référence des colonnes absentes du
    dataset, appelle le LLM pour adapter le code aux colonnes disponibles.

    Retourne une copie de la liste avec les skeletons corrigés (in-place sur dict).
    """
    import re, json as _json, ast as _ast
    from automatisation_2.prompts.skeleton_adapt_prompt import build_adaptation_messages

    # Colonnes réelles du dataset
    raw_feature_names = dataset.get("feature_names") or []
    if isinstance(raw_feature_names, str):
        try:
            raw_feature_names = _json.loads(raw_feature_names)
        except Exception:
            raw_feature_names = []

    if not raw_feature_names:
        return algorithms

    available_col_names = set(raw_feature_names)
    # Colonnes que le pipeline ajoute dynamiquement — pas à signaler comme manquantes
    pipeline_cols = {"anomaly_score", "is_anomaly", "corrected_value",
                     "corrected_lat", "corrected_lon"}
    available_col_names |= pipeline_cols

    # Construire les metadata colonnes pour le prompt
    schema_json = dataset.get("schema_json") or []
    if isinstance(schema_json, str):
        try:
            schema_json = _json.loads(schema_json)
        except Exception:
            schema_json = []

    col_meta = {c["name"]: c for c in schema_json if isinstance(c, dict) and "name" in c}
    available_columns = [
        {
            "name":        name,
            "dtype":       col_meta.get(name, {}).get("dtype", "unknown"),
            "description": col_meta.get(name, {}).get("description", ""),
        }
        for name in raw_feature_names
    ]

    # Aperçu des données pour le prompt
    data_sample = ""
    raw_json = dataset.get("data_json") or ""
    if isinstance(raw_json, str) and raw_json.startswith("["):
        try:
            rows = _json.loads(raw_json)
            data_sample = _json.dumps(rows[:5], ensure_ascii=False, default=str)
        except Exception:
            data_sample = raw_json[:500]

    # LLM — appel adaptatif uniquement si des colonnes manquent
    try:
        from shared.llm.router import chat_with_meta
    except ImportError:
        return algorithms

    adapted = []
    for algo in algorithms:
        skel = (algo.get("python_skeleton") or "").strip()
        if not skel:
            adapted.append(algo)
            continue

        # Colonnes lues par le skeleton (hors assignments et hors colonnes pipeline)
        cols_read = set(re.findall(r"df\[[\'\"](\w+)[\'\"]\]", skel))
        cols_assigned = set(re.findall(r"df\[[\'\"](\w+)[\'\"]\]\s*=", skel))
        cols_needed = cols_read - cols_assigned - pipeline_cols
        cols_missing = cols_needed - available_col_names

        if not cols_missing:
            # Skeleton déjà compatible — rien à faire
            adapted.append(algo)
            continue

        log.info(
            "[S2] Adaptation skeleton '%s' — colonnes manquantes : %s",
            algo.get("algorithm_key", "?"), cols_missing
        )

        try:
            messages = build_adaptation_messages(
                algo_name         = algo.get("name", ""),
                algo_description  = algo.get("description") or algo.get("principle") or "",
                problem_title     = problem.get("title", ""),
                available_columns = available_columns,
                data_sample       = data_sample,
                original_skeleton = skel,
                data_type_name    = data_type.get("name", ""),
            )
            resp = chat_with_meta(messages, temperature=0.15, profile="code")
            new_skel = (resp.get("content") or "").strip()
            # Nettoyer les balises markdown éventuelles
            if new_skel.startswith("```"):
                new_skel = re.sub(r"^```(?:python)?\n?", "", new_skel)
                new_skel = re.sub(r"\n?```$", "", new_skel).strip()

            if new_skel and len(new_skel) > 30:
                # Valider syntaxe
                try:
                    _ast.parse(new_skel)
                    algo = dict(algo)  # copie pour ne pas modifier l'original en cache
                    algo["python_skeleton"] = new_skel
                    algo["_skeleton_adapted"] = True
                    log.info("[S2] Skeleton adapté avec succès : %s", algo.get("algorithm_key"))
                except SyntaxError as e:
                    log.warning("[S2] Skeleton adapté invalide (SyntaxError %s) — skeleton original gardé", e)
            else:
                log.warning("[S2] LLM n'a pas retourné de skeleton valide pour %s", algo.get("algorithm_key"))
        except Exception as exc:
            log.warning("[S2] Adaptation LLM échouée pour %s : %s", algo.get("algorithm_key"), exc)

        adapted.append(algo)

    return adapted


def _deserialize_algorithms(algorithms: List[Dict]) -> None:
    json_fields = [
        "hyperparameters", "required_libraries", "evaluation_metrics",
        "input_format", "output_format",
    ]
    for algo in algorithms:
        for field in json_fields:
            val = algo.get(field)
            if isinstance(val, str) and val:
                try:
                    algo[field] = json.loads(val)
                except Exception:
                    algo[field] = []
        if isinstance(algo.get("python_skeleton"), str):
            algo["python_skeleton"] = (algo["python_skeleton"]
                                       .replace("\\n", "\n")
                                       .replace("\\t", "    "))
