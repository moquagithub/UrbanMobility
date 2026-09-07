"""
Step S1b — Enrichissement LLM des types de données.

Pour chaque type dans MySQL :
  1. Vérifie si l'enrichissement existe déjà (domain rempli)
  2. Appelle le LLM avec le prompt dédié
  3. Valide + sauvegarde dans data_types (domain, sources, use_cases...)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from shared.db.repository import CatalogueRepository, RunRepository
from shared.llm.client import call_llm_json
from shared.models import StepResult
from automatisation_1.prompts import enrich_types_prompt
from automatisation_1.validation.enrich_types_validator import validate_enrichment

log = logging.getLogger("auto1.s1b_enrich_types")

MAX_RETRIES = 2


def run(
    data_types: List[Dict],
    force: bool = False,
    type_filter: Optional[str] = None,
) -> StepResult:
    """
    Enrichit les types de données avec domaine, sources, cas d'usage, etc.
    """
    result = StepResult(step="s1b_enrich_types", success=False)
    cat_repo = CatalogueRepository()
    log_repo = RunRepository()

    if not data_types:
        return result.finish(False, "Aucun type à enrichir")

    if type_filter:
        tf = type_filter.lower()
        data_types = [
            dt for dt in data_types
            if tf in dt.get("id", "").lower() or tf in dt.get("name", "").lower()
        ]

    total_enriched = 0
    total_cached   = 0
    total_errors   = 0

    for dt in data_types:
        type_id   = dt.get("id", "")
        type_name = dt.get("name", type_id)
        desc      = dt.get("description", "")
        specs     = dt.get("specifications", "")

        log.info("[S1b] Enrichissement : %s", type_name)

        # Cache : si domain est déjà rempli, on saute
        if not force and dt.get("domain"):
            log.info("[S1b] [CACHE] %s déjà enrichi — skip", type_name)
            total_cached += 1
            continue

        messages = enrich_types_prompt.build_prompt(type_name, desc, specs)

        llm_result = call_llm_json(
            messages      = messages,
            expected_keys = ["domain", "sources", "use_cases"],
            max_tokens    = 1200,
            temperature   = 0.3,
            max_retries   = MAX_RETRIES,
            step          = "s1b_enrich_types",
            data_type_id  = type_id,
            log_repo      = log_repo,
            profile       = "text",
        )

        if not llm_result["success"]:
            err = f"LLM échec pour '{type_name}' : {llm_result['error']}"
            log.error("[S1b] %s", err)
            result.add_error(err)
            total_errors += 1
            continue

        validation = validate_enrichment(llm_result["data"])

        for w in validation.warnings:
            result.add_warning(f"{type_name} : {w}")

        if not validation.valid:
            for e in validation.errors:
                log.error("[S1b] INVALID %s — %s", type_name, e)
                result.add_error(f"{type_name} : {e}")
            total_errors += 1
            continue

        enrichment = validation.cleaned
        ok = cat_repo.update_enrichment(type_id, enrichment)
        if ok:
            total_enriched += 1
            log.info("[S1b] ✓ %s enrichi (domaine: %s)", type_name, enrichment.get("domain", "?"))
            import datetime as _dt
            from shared.storage.catalogue_storage import upload_type_metadata, upload_type_description
            _meta = {
                "id": type_id,
                "name": type_name,
                "description": desc,
                "specifications": specs,
                "domain": enrichment.get("domain"),
                "data_format": enrichment.get("data_format"),
                "sources": enrichment.get("sources"),
                "use_cases": enrichment.get("use_cases"),
                "typical_volume": enrichment.get("typical_volume"),
                "is_real_time": bool(enrichment.get("is_real_time")),
                "standardization": enrichment.get("standardization"),
                "data_challenges": enrichment.get("data_challenges"),
                "processing_status": "catalogue_done",
                "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
                "pipeline_step": "s1b_enrich_types",
            }
            _r = upload_type_metadata(type_id, _meta)
            if not _r.success:
                result.add_warning(
                    f"Minio upload type metadata (enrichi) échoué pour '{type_name}': {_r.error}"
                )
            _desc = (
                f"Type de données : {type_name}\n"
                f"Domaine : {enrichment.get('domain', '')}\n\n"
                f"Description:\n{desc}\n\n"
                f"Spécifications:\n{specs}\n\n"
                f"Format des données : {enrichment.get('data_format', '')}\n"
                f"Sources : {enrichment.get('sources', '')}\n"
                f"Volume typique : {enrichment.get('typical_volume', '')}\n"
                f"Temps réel : {'Oui' if enrichment.get('is_real_time') else 'Non'}\n"
                f"Standardisation : {enrichment.get('standardization', '')}\n\n"
                f"Cas d'usage:\n{enrichment.get('use_cases', '')}\n\n"
                f"Défis liés aux données:\n{enrichment.get('data_challenges', '')}\n"
            )
            upload_type_description(type_id, _desc)
        else:
            total_errors += 1
            result.add_error(f"Échec sauvegarde enrichissement pour '{type_name}'")

    result.data["total_enriched"] = total_enriched
    result.data["total_cached"]   = total_cached
    result.data["total_errors"]   = total_errors

    success = total_errors == 0 or total_enriched > 0
    msg = (
        f"{total_enriched} type(s) enrichi(s), "
        f"{total_cached} depuis cache, "
        f"{total_errors} erreur(s)"
    )
    log.info("[S1b] Terminé — %s", msg)
    return result.finish(success, msg)
