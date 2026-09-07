"""
backfill_catalogue_descriptions.py — Republie dans MinIO (bucket `catalogue`) les fichiers
description.txt / metadata.json / schema.json manquants pour les données déjà en MySQL.

Pourquoi ce script est nécessaire :
  Les steps S1/S1b/S2/S4 uploadent désormais un fichier texte lisible par répertoire
  (description.txt), en plus des metadata.json déjà existants. Mais ces steps ne
  ré-uploadent PAS quand la donnée est déjà en cache MySQL (problems_exist(), etc.) —
  donc tout ce qui a été généré AVANT l'ajout de ces uploads (ou avant l'ajout du
  cache lui-même) reste sans fichier dans MinIO tant qu'on ne force pas la régénération
  LLM complète. Ce script relit MySQL (source de vérité) et republie directement dans
  MinIO sans repasser par le LLM — rapide, gratuit, idempotent.

Usage :
    python backfill_catalogue_descriptions.py            # tout backfill
    python backfill_catalogue_descriptions.py --type gps_temps_r_el   # un seul type
    python backfill_catalogue_descriptions.py --skip-datasets         # sans les datasets (plus lent)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging

from shared.db.repository import CatalogueRepository, ProblemRepository, AlgorithmRepository
from shared.db.repository.dataset_repo import DatasetRepository
from shared.storage.catalogue_storage import (
    upload_type_metadata, upload_type_description,
    upload_problem_metadata, upload_problem_description,
    upload_dataset_schema, upload_dataset_description, upload_dataset_csv,
    problem_dir_prefix,
)
from automatisation_1.steps.s2_problems import _build_problem_description
from automatisation_1.steps.s4_datasets import _build_dataset_description

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill")


def _build_type_description(dt: dict) -> str:
    return (
        f"Type de données : {dt.get('name', '')}\n"
        f"Domaine : {dt.get('domain') or ''}\n\n"
        f"Description:\n{dt.get('description', '')}\n\n"
        f"Spécifications:\n{dt.get('specifications', '')}\n\n"
        f"Unités : {dt.get('units', '')}\n"
        f"Fréquence : {dt.get('frequency', '')}\n"
        f"Format des données : {dt.get('data_format') or ''}\n"
        f"Sources : {dt.get('sources') or ''}\n"
        f"Volume typique : {dt.get('typical_volume') or ''}\n"
        f"Temps réel : {'Oui' if dt.get('is_real_time') else 'Non'}\n"
        f"Standardisation : {dt.get('standardization') or ''}\n\n"
        f"Cas d'usage:\n{dt.get('use_cases') or ''}\n\n"
        f"Défis liés aux données:\n{dt.get('data_challenges') or ''}\n"
    )


def _backfill_type(dt: dict) -> None:
    type_id = dt["id"]
    _meta = {
        "id": type_id,
        "name": dt.get("name", ""),
        "description": dt.get("description", ""),
        "specifications": dt.get("specifications", ""),
        "units": dt.get("units", ""),
        "frequency": dt.get("frequency", ""),
        "domain": dt.get("domain"),
        "data_format": dt.get("data_format"),
        "sources": dt.get("sources"),
        "use_cases": dt.get("use_cases"),
        "typical_volume": dt.get("typical_volume"),
        "is_real_time": bool(dt.get("is_real_time")),
        "standardization": dt.get("standardization"),
        "data_challenges": dt.get("data_challenges"),
        "processing_status": dt.get("processing_status", ""),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "pipeline_step": "backfill",
    }
    upload_type_metadata(type_id, _meta)
    upload_type_description(type_id, _build_type_description(dt))
    log.info("[TYPE] %s — metadata.json + description.txt", type_id)


def _backfill_problem(type_id: str, prob: dict, prob_repo: ProblemRepository) -> None:
    prob_key = prob.get("problem_key", "")
    _pmeta = {
        "problem_key": prob_key,
        "data_type_id": type_id,
        "title": prob.get("title", ""),
        "description": prob.get("description", ""),
        "causes": prob.get("causes") or [],
        "consequences": prob.get("consequences", ""),
        "frequency": prob.get("frequency", ""),
        "impact_level": prob.get("impact_level", ""),
        "affected_actors": prob.get("affected_actors") or [],
        "detection_indicators": prob.get("detection_indicators") or [],
        "solutions_overview": prob.get("solutions_overview", ""),
        "data_requirements": prob.get("data_requirements", ""),
        "priority": prob.get("priority", 2),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "pipeline_step": "backfill",
    }
    _r = upload_problem_metadata(type_id, prob_key, _pmeta)
    upload_problem_description(type_id, prob_key, _build_problem_description(_pmeta))
    if _r.success and prob.get("id") and not prob.get("minio_dir"):
        prob_repo.update_minio_dir(prob["id"], problem_dir_prefix(type_id, prob_key))
    log.info("[PROBLEM] %s/%s — metadata.json + description.txt", type_id, prob_key)


def _backfill_dataset(type_id: str, prob_key: str, prob_title: str, ds: dict, with_csv: bool) -> None:
    dataset_key = ds.get("dataset_key", "")
    columns = ds.get("schema_json") or []
    _schema_payload = {
        "dataset_key": dataset_key,
        "data_type_id": type_id,
        "problem_key": prob_key,
        "problem_title": prob_title,
        "description": ds.get("description", ""),
        "n_rows": ds.get("n_samples") or ds.get("n_rows") or 0,
        "n_features": ds.get("n_features") or 0,
        "columns": columns,
        "stats": ds.get("stats_json") or {},
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "pipeline_step": "backfill",
    }
    upload_dataset_schema(type_id, prob_key, _schema_payload)
    upload_dataset_description(type_id, prob_key, _build_dataset_description(_schema_payload))
    log.info("[DATASET] %s/%s — schema.json + description.txt", type_id, prob_key)

    if with_csv:
        df = _load_dataframe(ds)
        if df is not None:
            _rc = upload_dataset_csv(type_id, prob_key, df)
            if _rc.success:
                log.info("[DATASET] %s/%s — data.csv (%d lignes)", type_id, prob_key, len(df))
            else:
                log.warning("[DATASET] %s/%s — échec upload data.csv : %s", type_id, prob_key, _rc.error)
        else:
            log.warning("[DATASET] %s/%s — data.csv introuvable (ni data_json, ni fichier, ni table)",
                        type_id, prob_key)


def _load_dataframe(ds: dict):
    """Reconstruit le DataFrame à partir de data_json, d'un CSV, ou de la table ds_{key}."""
    import pandas as pd

    data_json = ds.get("data_json")
    if data_json and isinstance(data_json, str) and not data_json.startswith("[MySQL table:"):
        try:
            data = json.loads(data_json)
            if isinstance(data, list) and data:
                return pd.DataFrame(data)
        except Exception:
            pass

    file_path = (ds.get("file_path") or "").strip()
    if file_path:
        from pathlib import Path
        p = Path(file_path)
        if p.suffix == ".csv" and p.exists():
            return pd.read_csv(p)
        if not p.suffix:
            # Référence à une table MySQL ds_xxx
            from shared.db.connection import get_connection
            conn = get_connection()
            if not conn:
                return None
            try:
                with conn.cursor(dictionary=True) as cur:
                    cur.execute(f"SELECT * FROM `{file_path}`")
                    rows = cur.fetchall() or []
                return pd.DataFrame(rows) if rows else None
            except Exception as exc:
                log.debug("Lecture table %s échouée : %s", file_path, exc)
                return None
            finally:
                conn.close()
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", dest="type_filter", default=None,
                        help="Limiter à un seul data_type_id")
    parser.add_argument("--skip-datasets", action="store_true",
                        help="Ne pas backfill les datasets (plus rapide)")
    parser.add_argument("--skip-csv", action="store_true",
                        help="Backfill schema.json/description.txt des datasets mais pas data.csv")
    args = parser.parse_args()

    cat_repo  = CatalogueRepository()
    prob_repo = ProblemRepository()
    ds_repo   = DatasetRepository()

    types = cat_repo.list_data_types()
    if args.type_filter:
        types = [t for t in types if t["id"] == args.type_filter]

    log.info("=== Backfill catalogue MinIO — %d type(s) ===", len(types))

    n_types = n_problems = n_datasets = 0

    for dt in types:
        type_id = dt["id"]
        _backfill_type(dt)
        n_types += 1

        problems = prob_repo.find_problems(type_id) or []
        for prob in problems:
            _backfill_problem(type_id, prob, prob_repo)
            n_problems += 1

            if args.skip_datasets:
                continue
            prob_key = prob.get("problem_key", "")
            dataset_key = f"{type_id}_{prob_key}"
            ds = ds_repo.find_dataset(type_id, dataset_key)
            if ds:
                _backfill_dataset(type_id, prob_key, prob.get("title", ""), ds,
                                  with_csv=not args.skip_csv)
                n_datasets += 1

    log.info("=== Terminé — %d type(s), %d problème(s), %d dataset(s) ===",
              n_types, n_problems, n_datasets)


if __name__ == "__main__":
    main()
