"""
Step 4 — Génération des datasets synthétiques pour chaque problème.

Pour chaque (type de données, problème) :
  1. Vérifie si le dataset existe déjà en MySQL (cache)
  2. Récupère les colonnes attendues par les algorithmes déjà générés (S3)
  3. Appelle le LLM pour générer un schéma de colonnes cohérent avec les algorithmes
  4. Génère un DataFrame synthétique avec anomalies injectées
  5. Sauvegarde en CSV + MySQL (table datasets)
  6. Met à jour processing_status → datasets_done

Ce step est dans Auto1 car les datasets sont directement liés aux problèmes
(générés par S2) et aux algorithmes (générés par S3) — les colonnes doivent
correspondre exactement à ce que les algorithmes référencent (df['xxx']).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Set

from shared.db.repository import (
    CatalogueRepository, ProblemRepository, DatasetRepository, AlgorithmRepository,
)
from shared.llm.client import call_llm_json
from shared.models import StepResult

from automatisation_2.prompts.dataset_prompt import build_prompt
from automatisation_2.validation.dataset_validator import validate_dataset_schema
from automatisation_2.data_generator import generate_dataframe, compute_stats
from automatisation_1.validation.execution_validator import validate_and_repair_problem_algorithms

log = logging.getLogger("auto1.s4_datasets")

# Colonnes système générées par le pipeline — pas besoin de les imposer au LLM
_PIPELINE_COLS = {"anomaly_score", "is_anomaly", "corrected_value",
                  "corrected_lat", "corrected_lon", "trend", "seasonal"}


def _build_dataset_description(schema_payload: Dict) -> str:
    """Construit un texte de présentation lisible à partir du schéma d'un dataset."""
    cols = schema_payload.get("columns") or []
    col_lines = [
        f"- {c.get('name', '')} ({c.get('dtype', '')}) : {c.get('description', '')}".rstrip(" :")
        for c in cols
    ]
    return (
        f"Dataset : {schema_payload.get('dataset_key', '')}\n"
        f"Problème associé : {schema_payload.get('problem_title', '')}\n\n"
        f"Description:\n{schema_payload.get('description', '')}\n\n"
        f"Lignes : {schema_payload.get('n_rows', '')}\n"
        f"Colonnes : {schema_payload.get('n_features', '')}\n\n"
        f"Schéma des colonnes:\n" + "\n".join(col_lines) + "\n"
    )


def _extract_algo_columns(skeleton: str) -> Set[str]:
    """Extrait les noms de colonnes référencés dans un squelette Python (df['xxx'] / df["xxx"])."""
    if not skeleton:
        return set()
    found = re.findall(r'df\[[\'"]([\w]+)[\'"]\]', skeleton)
    return {c for c in found if c not in _PIPELINE_COLS}


def _collect_problem_columns(prob_id: int) -> List[str]:
    """
    Récupère toutes les colonnes attendues par les algorithmes d'un problème.

    Lit la table `algorithms` pour ce prob_id et extrait les colonnes
    référencées dans python_skeleton.
    """
    try:
        import mysql.connector, os
        from dotenv import load_dotenv
        load_dotenv()
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "urbain_automation"),
        )
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT python_skeleton FROM algorithms WHERE problem_id = %s",
            (prob_id,),
        )
        rows = cur.fetchall()
        conn.close()

        cols: Set[str] = set()
        for row in rows:
            cols |= _extract_algo_columns(row.get("python_skeleton") or "")
        return sorted(cols)
    except Exception as exc:
        log.debug("[S4] _collect_problem_columns erreur : %s", exc)
        return []


def _repair_missing_columns(
    system_p: str,
    user_p: str,
    schema_raw: Dict,
    missing_cols: List[str],
    type_id: str,
) -> Optional[Dict]:
    """
    Appelle le LLM une 2ème fois avec un feedback précis sur les colonnes manquantes.

    Retourne le nouveau schema_raw (dict) ou None si l'appel échoue.
    """
    import json
    existing_cols = [c["name"] for c in schema_raw.get("columns", [])]
    repair_user_msg = (
        f"{user_p}\n\n"
        f"---\n"
        f"PROBLÈME DÉTECTÉ dans le schéma précédent : les colonnes suivantes sont requises par "
        f"les algorithmes de détection mais sont ABSENTES du schéma que tu as généré :\n"
        f"{missing_cols}\n\n"
        f"Colonnes actuellement présentes : {existing_cols}\n\n"
        f"Tu DOIS inclure les colonnes manquantes ({', '.join(missing_cols)}) dans le schéma. "
        f"Pour chaque colonne manquante, ajoute une entrée dans `columns` avec un type cohérent "
        f"(float, int, ou timestamp selon le contexte) et une description. "
        f"Retourne le schéma complet corrigé en JSON."
    )
    repair_result = call_llm_json(
        messages=[
            {"role": "system", "content": system_p},
            {"role": "user",   "content": repair_user_msg},
        ],
        max_tokens=3000,
        temperature=0.2,
        step="s4_datasets_repair",
        data_type_id=type_id,
        profile="json",
    )
    if not repair_result.get("success") or not repair_result.get("data"):
        log.warning("[S4] Réparation LLM erreur : %s", repair_result.get("error"))
        return None
    repaired = repair_result["data"]
    if isinstance(repaired, list):
        repaired = repaired[0] if repaired else {}
    return repaired if isinstance(repaired, dict) else None


def run(
    data_types: List[Dict],
    force: bool = False,
    type_filter: Optional[str] = None,
) -> StepResult:
    """
    Génère les datasets synthétiques pour tous les problèmes des types fournis.

    Args:
        data_types  : liste de dicts (id, name, description, specifications, ...)
        force       : régénère même si le dataset existe déjà en MySQL
        type_filter : traite uniquement ce type (id ou nom partiel)
    """
    result = StepResult(step="s4_datasets", success=False)

    cat_repo     = CatalogueRepository()
    prob_repo    = ProblemRepository()
    dataset_repo = DatasetRepository()
    alg_repo     = AlgorithmRepository()

    db_available = dataset_repo.is_available()

    if not data_types:
        return result.finish(False, "Aucun type de données fourni")

    if type_filter:
        tf = type_filter.lower()
        data_types = [
            dt for dt in data_types
            if tf in dt.get("id", "").lower() or tf in dt.get("name", "").lower()
        ]

    total_ok    = 0
    total_skip  = 0
    total_error = 0

    for dt in data_types:
        type_id   = dt.get("id", "")
        type_name = dt.get("name", type_id)
        domain    = dt.get("domain", "") or ""
        challenges= dt.get("data_challenges", "") or ""

        problems = prob_repo.find_problems(type_id) if db_available else []
        if not problems:
            log.warning("[S4] %s — aucun problème en DB, skip", type_name)
            total_skip += 1
            continue

        type_datasets_ok = 0

        for prob in problems:
            prob_id    = prob["id"]
            prob_key   = prob.get("problem_key", f"prob_{prob_id}")
            prob_title = prob.get("title", "")
            prob_desc  = prob.get("description", "")

            dataset_key = f"{type_id}_{prob_key}"

            # Cache : dataset déjà en MySQL ?
            if not force and db_available and dataset_repo.exists(type_id, dataset_key):
                log.info("[S4] [CACHE] %s/%s — dataset existant", type_name, prob_key)
                total_skip += 1
                type_datasets_ok += 1
                continue

            # Colonnes attendues par les algorithmes de ce problème
            algo_cols = _collect_problem_columns(prob_id)
            if algo_cols:
                log.info("[S4] %s/%s — colonnes algo détectées : %s", type_name, prob_key, algo_cols)
            else:
                log.info("[S4] %s/%s — aucune colonne algo trouvée (algorithms pas encore générés ?)", type_name, prob_key)

            log.info("[S4] %s / %s — génération du schéma via LLM…", type_name, prob_title[:50])

            system_p, user_p = build_prompt(
                type_name=type_name,
                domain=domain,
                problem_title=prob_title,
                problem_desc=prob_desc,
                data_challenges=challenges,
                algorithm_columns=algo_cols,
            )

            llm_result = call_llm_json(
                messages=[
                    {"role": "system", "content": system_p},
                    {"role": "user",   "content": user_p},
                ],
                max_tokens=3000,
                temperature=0.3,
                step="s4_datasets",
                data_type_id=type_id,
                profile="json",
            )

            if not llm_result.get("success") or not llm_result.get("data"):
                log.warning("[S4] %s/%s — LLM erreur : %s", type_name, prob_key, llm_result.get("error"))
                total_error += 1
                result.add_error(f"{type_name}/{prob_key} : {llm_result.get('error', 'LLM failure')}")
                continue

            schema_raw = llm_result["data"]
            if isinstance(schema_raw, list):
                schema_raw = schema_raw[0] if schema_raw else {}
            if not isinstance(schema_raw, dict):
                log.warning("[S4] %s/%s — réponse inattendue (type=%s)", type_name, prob_key, type(schema_raw))
                total_error += 1
                continue

            vr = validate_dataset_schema(schema_raw)
            if not vr.ok:
                log.warning("[S4] %s/%s — validation échouée : %s", type_name, prob_key, vr.errors)
                total_error += 1
                continue

            schema  = vr.data
            columns = schema["columns"]
            n_rows  = schema.get("n_rows", 5000)

            # ── Validation : colonnes algo présentes dans le schéma généré ──
            generated_col_names = {c["name"] for c in columns}
            missing = [c for c in algo_cols if c not in generated_col_names]
            if missing:
                log.warning(
                    "[S4] %s/%s — %d colonnes manquantes dans le schéma LLM : %s — "
                    "déclenchement réparation automatique",
                    type_name, prob_key, len(missing), missing,
                )
                repair_result = _repair_missing_columns(
                    system_p=system_p, user_p=user_p,
                    schema_raw=schema_raw, missing_cols=missing,
                    type_id=type_id,
                )
                if repair_result:
                    vr2 = validate_dataset_schema(repair_result)
                    if vr2.ok:
                        schema  = vr2.data
                        columns = schema["columns"]
                        n_rows  = schema.get("n_rows", n_rows)
                        still_missing = [c for c in algo_cols if c not in {col["name"] for col in columns}]
                        if still_missing:
                            log.warning("[S4] %s/%s — colonnes toujours manquantes après réparation : %s",
                                        type_name, prob_key, still_missing)
                        else:
                            log.info("[S4] %s/%s — schéma réparé avec succès (toutes colonnes présentes)", type_name, prob_key)
                    else:
                        log.warning("[S4] %s/%s — schéma réparé invalide : %s", type_name, prob_key, vr2.errors)
                else:
                    log.warning("[S4] %s/%s — réparation LLM échouée, on continue avec le schéma partiel", type_name, prob_key)

            # Colonnes numériques réellement utilisées par les algorithmes de ce problème
            # (lat/lon pour un saut GPS, timestamp pour une dérive d'horloge, etc.) —
            # c'est là qu'il faut injecter les anomalies, pas uniquement dans 'value'
            # comme avant (sinon les algos ciblant d'autres colonnes ne détectaient
            # jamais rien de réel, quel que soit le thème du problème).
            injectable_dtypes = {"float", "number", "int", "integer", "datetime"}
            schema_by_name = {c["name"]: c for c in columns}
            anomaly_cols = [
                c for c in algo_cols
                if c in schema_by_name and schema_by_name[c].get("dtype", "").lower() in injectable_dtypes
            ]

            try:
                df = generate_dataframe(
                    schema=columns,
                    n_rows=n_rows,
                    seed=hash(dataset_key) % 10000,
                    anomaly_columns=anomaly_cols,
                )
            except Exception as exc:
                log.warning("[S4] %s/%s — data_generator erreur : %s", type_name, prob_key, exc)
                total_error += 1
                continue

            if not db_available:
                log.warning("[S4] %s/%s — MySQL non disponible, dataset ignoré", type_name, prob_key)
                total_error += 1
                continue

            # Crée la table MySQL ds_{dataset_key} et insère toutes les lignes
            table_name = dataset_repo.create_and_populate_table(
                dataset_key    = dataset_key,
                df             = df,
                schema_columns = columns,
            )
            if not table_name:
                log.warning("[S4] %s/%s — création de table MySQL échouée", type_name, prob_key)
                total_error += 1
                continue

            log.info("[S4] Table `%s` créée (%d lignes, %d cols)",
                     table_name, len(df), len(df.columns))

            # Enregistre les métadonnées dans la table datasets (sans stocker les données brutes)
            ds_id = dataset_repo.save_dataset(
                data_type_id     = type_id,
                dataset_key      = dataset_key,
                data_json        = f"[MySQL table: {table_name}]",
                problem_id       = prob_id,
                description      = schema.get("description", ""),
                n_samples        = len(df),
                n_features       = len(df.columns),
                feature_names    = [c["name"] for c in columns],
                schema_json      = columns,
                stats_json       = compute_stats(df),
                file_path        = table_name,
                problem_context  = prob_title,
                generation_method= "synthetic_llm",
            )
            if ds_id:
                log.info("[S4] ✓ %s/%s → dataset#%d (table `%s`)",
                         type_name, prob_key, ds_id, table_name)
                total_ok += 1
                type_datasets_ok += 1
                # ── Upload Minio catalogue ────────────────────────────────
                try:
                    import datetime as _dt
                    from shared.storage.catalogue_storage import (
                        upload_dataset_schema, upload_dataset_csv, upload_dataset_description)
                    _schema_payload = {
                        "dataset_key": dataset_key,
                        "data_type_id": type_id,
                        "problem_key": prob_key,
                        "problem_title": prob_title,
                        "description": schema.get("description", ""),
                        "n_rows": len(df),
                        "n_features": len(df.columns),
                        "columns": columns,
                        "stats": compute_stats(df),
                        "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
                        "pipeline_step": "s4_datasets",
                    }
                    _rs = upload_dataset_schema(type_id, prob_key, _schema_payload)
                    _rc = upload_dataset_csv(type_id, prob_key, df)
                    upload_dataset_description(type_id, prob_key, _build_dataset_description(_schema_payload))
                    if not _rs.success:
                        log.warning("[S4] Minio schema upload échoué %s/%s: %s",
                                    type_id, prob_key, _rs.error)
                    if not _rc.success:
                        log.warning("[S4] Minio CSV upload échoué %s/%s: %s",
                                    type_id, prob_key, _rc.error)
                    if _rs.success or _rc.success:
                        from shared.storage.catalogue_storage import problem_dir_prefix
                        dataset_repo.update_minio_key(
                            ds_id,
                            problem_dir_prefix(type_id, prob_key) + "dataset/",
                        )
                except Exception as _exc:
                    log.warning("[S4] Minio upload erreur %s/%s: %s", type_id, prob_key, _exc)

                # ── Validation proactive par exécution réelle ────────────
                # Le dataset (df) et les skeletons (S3) sont tous les deux
                # disponibles ici — valider/réparer maintenant plutôt que
                # d'attendre la boucle réactive shared.quality --watch,
                # qui ne détecte ces erreurs qu'après un cycle Auto2 complet.
                try:
                    exec_summary = validate_and_repair_problem_algorithms(
                        type_id=type_id, type_name=type_name,
                        prob_id=prob_id, prob_key=prob_key, prob_title=prob_title,
                        df=df, alg_repo=alg_repo,
                    )
                    if exec_summary.repaired or exec_summary.failed:
                        log.info(
                            "[S4] %s/%s — validation exécution : %d ok, %d réparés, %d échoués (%s)",
                            type_name, prob_key, exec_summary.validated,
                            exec_summary.repaired, len(exec_summary.failed),
                            ", ".join(exec_summary.failed) or "-",
                        )
                    for failed_name in exec_summary.failed:
                        result.add_warning(
                            f"{type_name}/{prob_key}/{failed_name} : non réparable par exécution — "
                            "reste visible via shared.quality --watch"
                        )
                except Exception as _exc:
                    log.warning("[S4] Validation exécution erreur %s/%s: %s", type_id, prob_key, _exc)
            else:
                log.warning("[S4] %s/%s — sauvegarde métadonnées MySQL échouée", type_name, prob_key)
                total_error += 1

        # Mettre à jour le statut si au moins 1 dataset généré
        if db_available and type_datasets_ok > 0:
            cat_repo.update_status(type_id, "datasets_done")
            log.info("[S4] %s → datasets_done (%d datasets)", type_name, type_datasets_ok)

    result.data["total_ok"]    = total_ok
    result.data["total_skip"]  = total_skip
    result.data["total_error"] = total_error

    success = total_error == 0 or total_ok > 0
    msg = f"{total_ok} dataset(s) générés, {total_skip} en cache, {total_error} erreur(s)"
    log.info("[S4] Terminé — %s", msg)
    return result.finish(success, msg)
