"""
Step 2 — Génération des problèmes via LLM et sauvegarde en MySQL.

Pour chaque type de données :
  1. Vérifie si les problèmes existent déjà (cache MySQL)
  2. Appelle le LLM avec le prompt dédié
  3. Valide le JSON retourné (retry si invalide)
  4. Sauvegarde en MySQL + log de l'appel LLM
  5. Met à jour processing_status → problems_done

Mode enrichissement (enrich=True) :
  - Ne skip pas les types déjà traités
  - Passe les titres existants au LLM pour générer des problèmes DISTINCTS
  - Itère jusqu'à saturation (patience rounds sans nouveau problème) ou max_per_type
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from shared.db.repository import CatalogueRepository, ProblemRepository, RunRepository
from shared.llm.client import call_llm_json
from shared.models import DataType, StepResult
from automatisation_1.prompts import problems_prompt
from automatisation_1.validation.problem_validator import validate_problems

log = logging.getLogger("auto1.s2_problems")

MAX_RETRIES = 3


def _as_list(val) -> List:
    """Normalise une valeur potentiellement stockée en JSON string (lignes DB brutes) en liste."""
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return []
    return val if isinstance(val, list) else []


def _build_problem_description(pmeta: Dict) -> str:
    """Construit un texte de présentation lisible à partir des métadonnées d'un problème."""
    causes = _as_list(pmeta.get("causes"))
    actors = _as_list(pmeta.get("affected_actors"))
    indicators = _as_list(pmeta.get("detection_indicators"))
    return (
        f"Problème : {pmeta.get('title', '')}\n\n"
        f"Description:\n{pmeta.get('description', '')}\n\n"
        f"Causes:\n" + "\n".join(f"- {c}" for c in causes) + "\n\n"
        f"Conséquences:\n{pmeta.get('consequences', '')}\n\n"
        f"Fréquence : {pmeta.get('frequency', '')}\n"
        f"Niveau d'impact : {pmeta.get('impact_level', '')}\n"
        f"Priorité : {pmeta.get('priority', '')}\n\n"
        f"Acteurs concernés:\n" + "\n".join(f"- {a}" for a in actors) + "\n\n"
        f"Indicateurs de détection:\n" + "\n".join(f"- {i}" for i in indicators) + "\n\n"
        f"Aperçu des solutions:\n{pmeta.get('solutions_overview', '')}\n\n"
        f"Besoins en données:\n{pmeta.get('data_requirements', '')}\n"
    )


def _call_and_save(
    type_id: str,
    type_name: str,
    desc: str,
    specs: str,
    existing_titles: List[str],
    key_offset: int,
    prob_repo: ProblemRepository,
    cat_repo: CatalogueRepository,
    log_repo: RunRepository,
    result: StepResult,
    db_available: bool,
) -> int:
    """
    Fait un appel LLM pour obtenir 3 nouveaux problèmes, les valide et les sauvegarde.
    Retourne le nombre de nouveaux problèmes effectivement sauvés.
    """
    # Fetch full problem objects for "completion" framing (show title + brief desc to LLM)
    existing_problems = None
    if existing_titles and db_available:
        existing_problems = prob_repo.find_problems(type_id)

    messages = problems_prompt.build_prompt(
        type_name, desc, specs,
        existing_titles=existing_titles or None,
        existing_problems=existing_problems,
    )

    llm_result = call_llm_json(
        messages      = messages,
        expected_keys = ["problems"],
        max_tokens    = 2000,
        temperature   = 0.5,
        max_retries   = MAX_RETRIES,
        step          = "s2_problems",
        data_type_id  = type_id,
        log_repo      = log_repo,
        profile       = "json",
    )

    if not llm_result["success"]:
        err = f"LLM échec pour '{type_name}' : {llm_result['error']}"
        log.error("[S2] %s", err)
        result.add_error(err)
        return 0

    validation = validate_problems(llm_result["data"])

    for w in validation.warnings:
        log.warning("[S2] [WARN] %s — %s", type_name, w)
        result.add_warning(f"{type_name} : {w}")

    if not validation.valid:
        for e in validation.errors:
            log.error("[S2] [INVALID] %s — %s", type_name, e)
            result.add_error(f"{type_name} : {e}")
        return 0

    problems = validation.cleaned

    # Filtre les doublons (même titre déjà connu)
    existing_lower = {t.lower() for t in existing_titles}
    new_problems = [p for p in problems if p.get("title", "").lower() not in existing_lower]

    if not new_problems:
        log.info("[S2] Aucun nouveau problème distinct pour %s", type_name)
        return 0

    # Rekey pour éviter les collisions avec les clés existantes (p1, p2, p3 → p4, p5, ...)
    for i, p in enumerate(new_problems):
        p["key"] = f"p{key_offset + i + 1}"

    if db_available:
        saved_ids = prob_repo.save_problems(type_id, new_problems)
        nb_saved = len(saved_ids)
        if nb_saved > 0:
            cat_repo.update_status(type_id, "problems_done")
            log.info("[S2] ✓ %d nouveau(x) problème(s) sauvés pour %s", nb_saved, type_name)
            import datetime as _dt
            from shared.storage.catalogue_storage import (
                upload_problem_metadata, upload_problem_description, problem_dir_prefix
            )
            _now = _dt.datetime.utcnow().isoformat() + "Z"
            for p in new_problems:
                _pk = p.get("key", p.get("problem_key", ""))
                _db_id = saved_ids.get(_pk)
                _pmeta = {
                    "problem_key": _pk,
                    "data_type_id": type_id,
                    "title": p.get("title", ""),
                    "description": p.get("description", ""),
                    "causes": p.get("causes", []),
                    "consequences": p.get("consequences", ""),
                    "frequency": p.get("frequency", ""),
                    "impact_level": p.get("impact_level", ""),
                    "affected_actors": p.get("affected_actors", []),
                    "detection_indicators": p.get("detection_indicators", []),
                    "solutions_overview": p.get("solutions_overview", ""),
                    "data_requirements": p.get("data_requirements", ""),
                    "priority": p.get("priority", 2),
                    "generated_at": _now,
                    "pipeline_step": "s2_problems",
                }
                _r = upload_problem_metadata(type_id, _pk, _pmeta)
                if _r.success and _db_id:
                    prob_repo.update_minio_dir(_db_id, problem_dir_prefix(type_id, _pk))
                elif not _r.success:
                    log.warning("[S2] Minio upload problem metadata échoué %s/%s: %s",
                                type_id, _pk, _r.error)
                upload_problem_description(type_id, _pk, _build_problem_description(_pmeta))
        return nb_saved
    else:
        log.warning("[S2] MySQL indisponible — problèmes de '%s' non sauvés", type_name)
        return len(new_problems)


def run(
    data_types: List[Dict],
    force: bool = False,
    type_filter: Optional[str] = None,
    enrich: bool = False,
    max_per_type: int = 12,
    patience: int = 2,
) -> StepResult:
    """
    Génère les problèmes pour tous les types de données passés en argument.

    Args:
        data_types   : liste de dicts (id, name, description, specifications, ...)
        force        : si True, régénère même si les problèmes existent déjà
        type_filter  : si fourni, traite uniquement ce type (par id ou name)
        enrich       : si True, itère pour trouver plus de problèmes jusqu'à saturation
        max_per_type : (enrich) nombre max de problèmes à atteindre par type
        patience     : (enrich) rounds sans nouveau problème avant d'arrêter
    """
    result = StepResult(step="s2_problems", success=False)
    cat_repo  = CatalogueRepository()
    prob_repo = ProblemRepository()
    log_repo  = RunRepository()

    db_available = prob_repo.is_available()

    if not data_types:
        result.add_error("Aucun type de données fourni à l'étape 2")
        return result.finish(False)

    # Filtre optionnel
    if type_filter:
        tf = type_filter.lower()
        data_types = [
            dt for dt in data_types
            if tf in dt.get("id", "").lower() or tf in dt.get("name", "").lower()
        ]
        if not data_types:
            result.add_error(f"Aucun type trouvé avec le filtre '{type_filter}'")
            return result.finish(False)

    total_saved  = 0
    total_cached = 0
    total_errors = 0

    for dt in data_types:
        type_id   = dt.get("id", "")
        type_name = dt.get("name", type_id)
        desc      = dt.get("description", "")
        specs     = dt.get("specifications", "")

        log.info("[S2] Traitement : %s (%s)", type_name, type_id)

        # ── Mode normal (sans enrichissement) ───────────────────────────────
        if not enrich:
            if db_available and not force:
                if prob_repo.problems_exist(type_id):
                    count = prob_repo.count_problems(type_id)
                    log.info("[S2] [CACHE] %d problème(s) déjà en MySQL pour %s — skip",
                             count, type_name)
                    total_cached += count
                    continue

            nb = _call_and_save(
                type_id, type_name, desc, specs,
                existing_titles=[],
                key_offset=0,
                prob_repo=prob_repo, cat_repo=cat_repo, log_repo=log_repo,
                result=result, db_available=db_available,
            )
            if nb > 0:
                total_saved += nb
            else:
                total_errors += 1
            continue

        # ── Mode enrichissement ──────────────────────────────────────────────
        # Récupère les problèmes existants
        existing = prob_repo.find_problems(type_id) if db_available else []
        existing_titles = [p.get("title", "") for p in existing]
        current_count   = len(existing_titles)

        if current_count >= max_per_type:
            log.info("[S2] [ENRICH] %s déjà saturé (%d >= %d) — skip",
                     type_name, current_count, max_per_type)
            total_cached += current_count
            continue

        log.info("[S2] [ENRICH] %s — %d problèmes existants, cible %d",
                 type_name, current_count, max_per_type)

        consecutive_empty = 0
        round_num = 0

        while current_count < max_per_type and consecutive_empty < patience:
            round_num += 1
            log.info("[S2] [ENRICH] %s — round %d (existants: %d/%d, patience: %d/%d)",
                     type_name, round_num, current_count, max_per_type,
                     consecutive_empty, patience)

            nb = _call_and_save(
                type_id, type_name, desc, specs,
                existing_titles=existing_titles,
                key_offset=current_count,
                prob_repo=prob_repo, cat_repo=cat_repo, log_repo=log_repo,
                result=result, db_available=db_available,
            )

            if nb == 0:
                consecutive_empty += 1
                log.info("[S2] [ENRICH] Round %d sans nouveau problème (patience %d/%d)",
                         round_num, consecutive_empty, patience)
            else:
                consecutive_empty = 0
                total_saved += nb
                current_count += nb
                # Rafraîchit la liste des titres existants depuis la DB
                if db_available:
                    existing_titles = [
                        p.get("title", "") for p in prob_repo.find_problems(type_id)
                    ]

        if consecutive_empty >= patience:
            log.info("[S2] [ENRICH] %s saturé après %d rounds — %d problèmes",
                     type_name, round_num, current_count)
        else:
            log.info("[S2] [ENRICH] %s — max atteint (%d problèmes)", type_name, current_count)

    result.data["total_saved"]    = total_saved
    result.data["total_cached"]   = total_cached
    result.data["total_errors"]   = total_errors
    result.data["types_processed"] = len(data_types)

    success = total_errors == 0 or total_saved > 0
    msg = (
        f"{total_saved} problème(s) sauvés, "
        f"{total_cached} depuis cache, "
        f"{total_errors} erreur(s)"
    )
    log.info("[S2] Terminé — %s", msg)
    return result.finish(success, msg)
