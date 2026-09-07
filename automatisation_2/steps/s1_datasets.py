"""
Étape S1 Auto2 — Chargement des datasets (load-or-create).

Stratégie :
  1. Vérifie si le dataset existe déjà en MySQL (table datasets, généré par Auto1 S4)
     → si oui : utilise le chemin CSV ou reconstruit depuis data_json
  2. Si absent : génère le dataset via LLM (fallback — Auto1 S4 aurait dû le faire)

La source de vérité est MySQL. Le CSV sur disque est secondaire.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from shared.db.repository import CatalogueRepository, ProblemRepository, DatasetRepository
from shared.llm.client import call_llm_json

from automatisation_2.prompts.dataset_prompt import build_prompt
from automatisation_2.validation.dataset_validator import validate_dataset_schema
from automatisation_2.data_generator import generate_dataframe, compute_stats, df_to_json_str

log = logging.getLogger("auto2.s1_datasets")

DATASETS_DIR = Path(tempfile.gettempdir()) / "mobility_pipeline" / "datasets"


def _load_or_create(
    type_id: str,
    type_name: str,
    prob_id: int,
    prob_key: str,
    prob_title: str,
    prob_desc: str,
    domain: str,
    challenges: str,
    dataset_repo: DatasetRepository,
    force: bool,
) -> Optional[Dict]:
    """
    Retourne les infos du dataset pour ce problème (depuis MySQL ou en créant).
    Retourne None en cas d'échec.
    """
    dataset_key = f"{type_id}_{prob_key}"
    csv_path    = DATASETS_DIR / f"{dataset_key}.csv"

    # ── 1. Déjà en MySQL ? ────────────────────────────────────────────────────
    if not force and dataset_repo.exists(type_id, dataset_key):
        ds = dataset_repo.find_dataset(type_id, dataset_key)
        if ds:
            # Vérifier que le CSV existe encore (peut avoir été supprimé)
            existing_path = ds.get("file_path", "")
            if existing_path and Path(existing_path).exists():
                log.info("[S1] [MYSQL] %s/%s — dataset#%d chargé (CSV: %s)",
                         type_name, prob_key, ds.get("id", 0), existing_path)
                return ds
            # CSV absent → reconstituer depuis data_json
            if ds.get("data_json"):
                try:
                    df = _reconstruct_df(ds["data_json"])
                    df.to_csv(csv_path, index=False)
                    log.info("[S1] [MYSQL] %s/%s — CSV reconstruit depuis data_json (%d lignes)",
                             type_name, prob_key, len(df))
                    dataset_repo.update_file_path(ds["id"], str(csv_path.resolve()))
                    ds["file_path"] = str(csv_path.resolve())
                    return ds
                except Exception as exc:
                    log.warning("[S1] %s/%s — reconstruction df échouée : %s", type_name, prob_key, exc)
            # Dataset en MySQL mais données inutilisables → recréer
            log.warning("[S1] %s/%s — dataset en MySQL sans données valides, recréation", type_name, prob_key)

    # ── 2. Générer via LLM (fallback) ─────────────────────────────────────────
    log.info("[S1] [LLM] %s/%s — génération dataset via LLM…", type_name, prob_title[:50])

    system_p, user_p = build_prompt(
        type_name=type_name,
        domain=domain,
        problem_title=prob_title,
        problem_desc=prob_desc,
        data_challenges=challenges,
    )

    llm_result = call_llm_json(
        messages=[{"role": "system", "content": system_p}, {"role": "user", "content": user_p}],
        max_tokens=1200,
        temperature=0.3,
        step="s1_datasets",
        data_type_id=type_id,
    )

    if not llm_result.get("success") or not llm_result.get("data"):
        log.warning("[S1] %s/%s — LLM erreur : %s", type_name, prob_key, llm_result.get("error"))
        return None

    schema_raw = llm_result["data"]
    if isinstance(schema_raw, list):
        schema_raw = schema_raw[0] if schema_raw else {}
    if not isinstance(schema_raw, dict):
        return None

    vr = validate_dataset_schema(schema_raw)
    if not vr.ok:
        log.warning("[S1] %s/%s — validation échouée : %s", type_name, prob_key, vr.errors)
        return None

    schema  = vr.data
    columns = schema["columns"]
    n_rows  = schema.get("n_rows", 5000)

    try:
        df = generate_dataframe(schema=columns, n_rows=n_rows, seed=hash(dataset_key) % 10000)
    except Exception as exc:
        log.warning("[S1] %s/%s — data_generator erreur : %s", type_name, prob_key, exc)
        return None

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(csv_path, index=False)
        ds_id = dataset_repo.save_dataset(
            data_type_id     = type_id,
            dataset_key      = dataset_key,
            data_json        = df_to_json_str(df),
            problem_id       = prob_id,
            description      = schema.get("description", ""),
            n_samples        = len(df),
            n_features       = len(df.columns),
            feature_names    = [c["name"] for c in columns],
            schema_json      = columns,
            stats_json       = compute_stats(df),
            file_path        = str(csv_path.resolve()),
            problem_context  = prob_title,
            generation_method= "synthetic_llm",
        )
        log.info("[S1] ✓ %s/%s — dataset#%s créé (%d lignes)", type_name, prob_key, ds_id, len(df))
        return dataset_repo.find_dataset(type_id, dataset_key)
    except Exception:
        csv_path.unlink(missing_ok=True)
        raise


def _reconstruct_df(data_json: str) -> pd.DataFrame:
    """Reconstruit un DataFrame depuis le JSON stocké en MySQL."""
    data = json.loads(data_json)
    if isinstance(data, list):
        return pd.DataFrame(data)
    if isinstance(data, dict):
        return pd.DataFrame.from_dict(data)
    raise ValueError(f"Format data_json inattendu : {type(data)}")


def run(
    type_ids: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, int]:
    """
    Charge (ou crée) les datasets pour tous les types de données.

    Retourne {"ok": N, "skip": N, "error": N}
    """
    cat_repo     = CatalogueRepository()
    prob_repo    = ProblemRepository()
    dataset_repo = DatasetRepository()

    types = cat_repo.list_data_types()
    if type_ids:
        types = [t for t in types if t["id"] in type_ids]

    stats = {"ok": 0, "skip": 0, "error": 0}

    for dt in types:
        type_id    = dt["id"]
        type_name  = dt["name"]
        domain     = dt.get("domain", "") or ""
        challenges = dt.get("data_challenges", "") or ""

        problems = prob_repo.find_problems(type_id)
        if not problems:
            log.warning("[S1] %s — aucun problème en DB, skip", type_name)
            stats["skip"] += 1
            continue

        for prob in problems:
            ds = _load_or_create(
                type_id=type_id, type_name=type_name,
                prob_id=prob["id"],
                prob_key=prob.get("problem_key", f"prob_{prob['id']}"),
                prob_title=prob.get("title", ""),
                prob_desc=prob.get("description", ""),
                domain=domain, challenges=challenges,
                dataset_repo=dataset_repo,
                force=force,
            )
            if ds:
                stats["ok"] += 1
            else:
                stats["error"] += 1

    log.info("[S1] Terminé — ok=%d skip=%d error=%d", stats["ok"], stats["skip"], stats["error"])
    return stats
