"""
Step 3 — Génération des algorithmes enrichis via LLM.

Un appel LLM par algorithme (pas 3 à la fois) pour éviter les troncatures JSON.
Pour chaque problème de chaque type :
  1. Cache MySQL — skip si algorithmes déjà présents
  2. Génère 3 algorithmes de familles différentes (1 appel par algo)
  3. Valide + sauvegarde en MySQL
  4. Met à jour processing_status → algorithms_done

Mode enrichissement (enrich=True) :
  - Ne skip pas les problèmes déjà traités
  - Passe les noms existants au LLM pour générer des algorithmes DISTINCTS
  - Itère jusqu'à saturation (patience rounds sans nouvel algo) ou max_per_problem
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from shared.db.repository import (
    CatalogueRepository,
    ProblemRepository,
    AlgorithmRepository,
    RunRepository,
)
from shared.llm.client import call_llm_json
from shared.models import StepResult
from automatisation_1.prompts.algorithms_prompt import (
    build_prompt, build_repair_prompt, CATEGORY_HINTS
)
from automatisation_1.validation.algorithm_validator import validate_algorithms
from automatisation_1.validation.skeleton_validator import (
    validate_skeleton, build_skeleton_repair_messages
)

log = logging.getLogger("auto1.s3_algorithms")

MAX_RETRIES = 3


def _upload_problem_test_results(
    type_id: str,
    prob_key: str,
    prob_title: str,
    prob_id,
    alg_repo,
    db_available: bool,
) -> None:
    """Génère et upload le test_results.json pour un problème après tous ses algos."""
    try:
        import datetime as _dt
        from shared.storage.catalogue_storage import upload_test_results, algorithm_dir_prefix
        final_algos = alg_repo.find_algorithms(prob_id) if (db_available and prob_id) else []
        payload = {
            "problem_key": prob_key,
            "data_type_id": type_id,
            "problem_title": prob_title,
            "total_algorithms": len(final_algos),
            "algorithms": [
                {
                    "algorithm_key": a.get("algorithm_key", ""),
                    "name": a.get("name", ""),
                    "skeleton_valid": True,
                    "minio_dir": algorithm_dir_prefix(
                        type_id, prob_key, a.get("algorithm_key", "")
                    ),
                }
                for a in final_algos
            ],
            "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
            "pipeline_step": "s3_algorithms",
        }
        _r = upload_test_results(type_id, prob_key, payload)
        if not _r.success:
            log.warning("[S3] Minio upload test_results échoué %s/%s: %s",
                        type_id, prob_key, _r.error)
    except Exception as exc:
        log.warning("[S3] _upload_problem_test_results erreur %s/%s: %s",
                    type_id, prob_key, exc)
MAX_SKELETON_REPAIRS = 2  # tentatives de réparation du squelette après génération
NB_ALGORITHMS_PER_PROBLEM = 3


def _validate_single(data: Dict, index: int):
    """Adapte un dict algo unique en format liste pour le validator."""
    from automatisation_1.validation.algorithm_validator import validate_algorithms as _val
    return _val({"algorithms": [data]})


def _generate_algorithms_for_problem(
    type_name: str,
    type_id: str,
    prob_id,
    prob_key: str,
    prob_title: str,
    prob_desc: str,
    prob_causes: List,
    existing_names: List[str],
    algo_start_idx: int,
    nb_to_generate: int,
    alg_repo: AlgorithmRepository,
    log_repo: RunRepository,
    result: StepResult,
    db_available: bool,
) -> List[Dict]:
    """
    Génère nb_to_generate nouveaux algorithmes pour un problème donné.
    Retourne la liste des algorithmes validés et sauvegardés.
    """
    # Fetch full algo objects once for "completion" framing (show family coverage to LLM)
    existing_algos_full = None
    if existing_names and db_available and prob_id:
        existing_algos_full = alg_repo.find_algorithms(prob_id)

    generated = []
    for algo_idx in range(algo_start_idx, algo_start_idx + nb_to_generate):
        cat_idx = ((algo_idx - 1) % len(CATEGORY_HINTS))
        category_hint = CATEGORY_HINTS[cat_idx] if CATEGORY_HINTS else ""

        # Merge DB algos + already-generated-this-call for full coverage context
        all_existing_for_framing = (existing_algos_full or []) + generated

        messages = build_prompt(
            type_name               = type_name,
            problem_title           = prob_title,
            problem_description     = prob_desc,
            problem_causes          = prob_causes,
            algorithm_index         = algo_idx,
            existing_algorithm_names= existing_names + [a.get("name", "") for a in generated],
            existing_algorithms     = all_existing_for_framing if all_existing_for_framing else None,
            algorithm_category_hint = category_hint,
        )

        llm_result = call_llm_json(
            messages      = messages,
            expected_keys = ["name", "principle", "math_formulation"],
            max_tokens    = 3500,
            temperature   = 0.5,
            max_retries   = MAX_RETRIES,
            step          = "s3_algorithms",
            data_type_id  = type_id,
            problem_id    = prob_id,
            log_repo      = log_repo,
            profile       = "code",
        )

        if not llm_result["success"]:
            err = f"LLM échec alg{algo_idx}/{prob_key}/{type_name} : {llm_result['error']}"
            log.error("[S3] %s", err)
            result.add_error(err)
            continue

        raw_data = llm_result["data"]
        if "key" not in raw_data:
            raw_data["key"] = f"alg{algo_idx}"

        # ── Validation + réparation automatique du squelette Python ──────
        current_messages = messages
        for repair_attempt in range(MAX_SKELETON_REPAIRS + 1):
            skeleton = raw_data.get("python_skeleton", "")
            skel_val = validate_skeleton(skeleton)

            if skel_val.ok:
                if skel_val.warnings:
                    for w in skel_val.warnings:
                        log.debug("[S3] skeleton warning %s/%s/alg%d : %s",
                                  type_name, prob_key, algo_idx, w)
                break  # squelette valide

            log.warning("[S3] Squelette invalide %s/%s/alg%d (tentative %d/%d) : %s",
                        type_name, prob_key, algo_idx,
                        repair_attempt + 1, MAX_SKELETON_REPAIRS + 1,
                        skel_val.errors)

            if repair_attempt >= MAX_SKELETON_REPAIRS:
                log.error("[S3] Squelette non réparable après %d tentatives — algo rejeté",
                          MAX_SKELETON_REPAIRS + 1)
                raw_data = None
                break

            # Appel LLM de réparation avec feedback précis
            repair_messages = build_skeleton_repair_messages(
                current_messages, skeleton, skel_val
            )
            repair_result = call_llm_json(
                messages      = repair_messages,
                expected_keys = ["python_skeleton"],
                max_tokens    = 2200,
                temperature   = 0.3,
                max_retries   = 1,
                step          = "s3_algorithms_repair",
                data_type_id  = type_id,
                problem_id    = prob_id,
                log_repo      = log_repo,
                profile       = "code",
            )
            if repair_result["success"] and repair_result.get("data"):
                repaired = repair_result["data"]
                # Mettre à jour uniquement le squelette (garder le reste du raw_data)
                raw_data["python_skeleton"] = repaired.get("python_skeleton", skeleton)
                current_messages = repair_messages
                log.info("[S3] Squelette réparé (tentative %d)", repair_attempt + 1)
            else:
                log.warning("[S3] LLM réparation échoué : %s", repair_result.get("error"))
                break

        if raw_data is None:
            result.add_error(f"{type_name}/{prob_key}/alg{algo_idx} : squelette non réparable")
            continue

        # ── Validation JSON structure ─────────────────────────────────────
        validation = validate_algorithms({"algorithms": [raw_data]})

        for w in validation.warnings:
            result.add_warning(f"{type_name}/{prob_key}/alg{algo_idx} : {w}")

        if not validation.valid:
            for e in validation.errors:
                log.error("[S3] INVALID %s/%s/alg%d — %s", type_name, prob_key, algo_idx, e)
                result.add_error(f"{type_name}/{prob_key}/alg{algo_idx} : {e}")
            continue

        alg_clean = validation.cleaned[0]
        generated.append(alg_clean)
        log.info("[S3] ✓ alg%d pour %s/%s : %s", algo_idx, prob_key, type_id, alg_clean["name"][:60])

    # Sauvegarde + uploads Minio par algorithme
    if generated and db_available and prob_id:
        saved_ids = alg_repo.save_algorithms(prob_id, generated)
        log.info("[S3] ✓ %d algo(s) sauvés pour %s/%s", len(saved_ids), prob_key, type_id)

        import datetime as _dt
        from shared.storage.catalogue_storage import (
            upload_algorithm_metadata,
            upload_algorithm_skeleton,
            upload_algorithm_explanation,
            algorithm_dir_prefix,
        )
        _now = _dt.datetime.utcnow().isoformat() + "Z"
        for alg in generated:
            _ak = alg.get("key", alg.get("algorithm_key", ""))
            _db_id = saved_ids.get(_ak)
            _ameta = {
                "algorithm_key": _ak,
                "problem_key": prob_key,
                "data_type_id": type_id,
                "name": alg.get("name", ""),
                "category": alg.get("category", ""),
                "principle": alg.get("principle", ""),
                "math_formulation": alg.get("math_formulation", ""),
                "complexity_time": alg.get("complexity_time", ""),
                "complexity_space": alg.get("complexity_space", ""),
                "pseudocode": alg.get("pseudocode", ""),
                "input_format": alg.get("input_format", ""),
                "output_format": alg.get("output_format", ""),
                "hyperparameters": alg.get("hyperparameters", []),
                "required_libraries": alg.get("required_libraries", []),
                "evaluation_metrics": alg.get("evaluation_metrics", []),
                "use_case_example": alg.get("use_case_example", ""),
                "generated_at": _now,
                "pipeline_step": "s3_algorithms",
            }
            _r1 = upload_algorithm_metadata(type_id, prob_key, _ak, _ameta)
            _r2 = upload_algorithm_skeleton(type_id, prob_key, _ak,
                                            alg.get("python_skeleton", ""))
            _expl = (
                f"Algorithm: {alg.get('name', '')}\n"
                f"Category: {alg.get('category', '')}\n\n"
                f"Principle:\n{alg.get('principle', '')}\n\n"
                f"Mathematical Formulation:\n{alg.get('math_formulation', '')}\n\n"
                f"Use Case Example:\n{alg.get('use_case_example', '')}\n"
            )
            _r3 = upload_algorithm_explanation(type_id, prob_key, _ak, _expl)
            if _db_id and (_r1.success or _r2.success):
                alg_repo.update_minio_dir(_db_id, algorithm_dir_prefix(type_id, prob_key, _ak))
            if not all(r.success for r in [_r1, _r2, _r3]):
                log.warning("[S3] Minio upload partiel pour %s/%s/%s", type_id, prob_key, _ak)

    return generated


def run(
    data_types: List[Dict],
    force: bool = False,
    type_filter: Optional[str] = None,
    enrich: bool = False,
    max_per_problem: int = 9,
    patience: int = 2,
) -> StepResult:
    """
    Génère les algorithmes pour tous les problèmes des types de données fournis.

    Args:
        data_types      : liste de dicts (id, name, description, ...)
        force           : si True, régénère même si les algos existent déjà
        type_filter     : si fourni, traite uniquement ce type
        enrich          : si True, itère pour trouver plus d'algos jusqu'à saturation
        max_per_problem : (enrich) nb max d'algorithmes par problème
        patience        : (enrich) rounds sans nouvel algo avant d'arrêter
    """
    result   = StepResult(step="s3_algorithms", success=False)
    cat_repo = CatalogueRepository()
    prob_repo= ProblemRepository()
    alg_repo = AlgorithmRepository()
    log_repo = RunRepository()

    db_available = alg_repo.is_available()

    if not data_types:
        return result.finish(False, "Aucun type de données fourni")

    if type_filter:
        tf = type_filter.lower()
        data_types = [
            dt for dt in data_types
            if tf in dt.get("id", "").lower() or tf in dt.get("name", "").lower()
        ]

    total_saved  = 0
    total_cached = 0
    total_errors = 0

    for dt in data_types:
        import json as _json

        type_id   = dt.get("id", "")
        type_name = dt.get("name", type_id)

        log.info("[S3] Type : %s (%s)", type_name, type_id)

        problems = prob_repo.find_problems(type_id) if db_available else dt.get("_problems", [])
        if not problems:
            log.warning("[S3] Aucun problème pour %s — skip", type_name)
            result.add_warning(f"Aucun problème pour '{type_name}' — exécutez S2 d'abord")
            continue

        existing_names = alg_repo.get_existing_names(type_id) if db_available else []
        type_algo_count = 0

        for problem in problems:
            prob_id    = problem.get("id")
            prob_key   = problem.get("problem_key", problem.get("key", ""))
            prob_title = problem.get("title", "")
            prob_desc  = problem.get("description", "")
            prob_causes= problem.get("causes", [])
            if isinstance(prob_causes, str):
                try:
                    prob_causes = _json.loads(prob_causes)
                except Exception:
                    prob_causes = []

            log.info("[S3] Problème %s/%s : %s", prob_key, type_id, prob_title[:60])

            # ── Mode normal ────────────────────────────────────────────────
            if not enrich:
                if db_available and prob_id and not force:
                    if alg_repo.algorithms_exist(prob_id):
                        nb = len(alg_repo.find_algorithms(prob_id))
                        log.info("[S3] [CACHE] %d algo(s) en MySQL pour %s/%s — skip",
                                 nb, prob_key, type_id)
                        total_cached += nb
                        continue

                generated = _generate_algorithms_for_problem(
                    type_name, type_id, prob_id, prob_key,
                    prob_title, prob_desc, prob_causes,
                    existing_names=existing_names,
                    algo_start_idx=1,
                    nb_to_generate=NB_ALGORITHMS_PER_PROBLEM,
                    alg_repo=alg_repo, log_repo=log_repo,
                    result=result, db_available=db_available,
                )
                if generated:
                    total_saved += len(generated)
                    type_algo_count += len(generated)
                    existing_names.extend([a["name"] for a in generated])
                else:
                    total_errors += 1
                _upload_problem_test_results(
                    type_id, prob_key, prob_title, prob_id, alg_repo, db_available)
                continue

            # ── Mode enrichissement ───────────────────────────────────────
            existing_algos = alg_repo.find_algorithms(prob_id) if (db_available and prob_id) else []
            existing_algo_names = [a.get("name", "") for a in existing_algos]
            current_count = len(existing_algo_names)

            if current_count >= max_per_problem:
                log.info("[S3] [ENRICH] %s/%s déjà saturé (%d >= %d) — skip",
                         type_name, prob_key, current_count, max_per_problem)
                total_cached += current_count
                continue

            log.info("[S3] [ENRICH] %s/%s — %d algos existants, cible %d",
                     type_name, prob_key, current_count, max_per_problem)

            consecutive_empty = 0
            round_num = 0

            while current_count < max_per_problem and consecutive_empty < patience:
                round_num += 1
                nb_to_gen = min(NB_ALGORITHMS_PER_PROBLEM, max_per_problem - current_count)
                log.info("[S3] [ENRICH] %s/%s — round %d (%d algos existants, +%d)",
                         type_name, prob_key, round_num, current_count, nb_to_gen)

                generated = _generate_algorithms_for_problem(
                    type_name, type_id, prob_id, prob_key,
                    prob_title, prob_desc, prob_causes,
                    existing_names=existing_names + existing_algo_names,
                    algo_start_idx=current_count + 1,
                    nb_to_generate=nb_to_gen,
                    alg_repo=alg_repo, log_repo=log_repo,
                    result=result, db_available=db_available,
                )

                # Filtre les vrais nouveaux (noms distincts)
                known_lower = {n.lower() for n in existing_algo_names}
                truly_new = [a for a in generated if a.get("name", "").lower() not in known_lower]

                if not truly_new:
                    consecutive_empty += 1
                    log.info("[S3] [ENRICH] Round %d sans nouvel algo (patience %d/%d)",
                             round_num, consecutive_empty, patience)
                else:
                    consecutive_empty = 0
                    total_saved += len(truly_new)
                    type_algo_count += len(truly_new)
                    existing_algo_names.extend([a["name"] for a in truly_new])
                    existing_names.extend([a["name"] for a in truly_new])
                    current_count += len(truly_new)

            # test_results.json après saturation du problème (mode enrichissement)
            _upload_problem_test_results(
                type_id, prob_key, prob_title, prob_id, alg_repo, db_available)

        if db_available and type_algo_count > 0:
            cat_repo.update_status(type_id, "algorithms_done")
            log.info("[S3] Type %s → algorithms_done (%d algos)", type_name, type_algo_count)

    result.data["total_saved"]  = total_saved
    result.data["total_cached"] = total_cached
    result.data["total_errors"] = total_errors

    success = total_errors == 0 or total_saved > 0
    msg = (
        f"{total_saved} algorithme(s) sauvés, "
        f"{total_cached} depuis cache, "
        f"{total_errors} erreur(s)"
    )
    log.info("[S3] Terminé — %s", msg)
    return result.finish(success, msg)
