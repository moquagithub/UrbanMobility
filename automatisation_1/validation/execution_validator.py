"""
Validation PROACTIVE des skeletons d'algorithmes par exécution réelle, appelée
juste après la génération du dataset (S4) — pas seulement réactivement, des
heures plus tard, une fois qu'Auto2 a exécuté un notebook Jupyter complet.

Contexte : shared/quality/checks.py détecte déjà `algo_execution_error` et
`algo_miscalibrated`, mais seulement en lisant notebook_results — c'est-à-dire
après tout le cycle Auto1 → Auto2 (build notebook + exécution nbconvert, coûteux
en temps). shared/quality/repair.py sait déjà réparer ces problèmes une fois
détectés, via une validation par exécution réelle en sous-processus isolé
(shared/validation/skeleton_execution.py). Ce module applique la MÊME validation
d'exécution, mais tout de suite après S4, quand le skeleton (S3) et le dataset
(S4) sont tous les deux déjà disponibles — avant même qu'Auto2 ne démarre.

But : pour un nouveau type de données, la plupart des erreurs d'exécution et
mauvais calibrages sont corrigés ici, avant insertion définitive — la boucle
lente de réparation réactive (quality --watch) ne reste qu'un filet de sécurité
pour les cas résiduels.
"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from shared.validation.skeleton_execution import validate_skeleton_execution

log = logging.getLogger("auto1.execution_validator")

MAX_EXECUTION_REPAIRS = 2  # tentatives de réparation par exécution après génération


@dataclass
class ExecutionValidationSummary:
    validated: int = 0   # skeletons déjà corrects dès le premier essai
    repaired: int = 0    # skeletons corrigés avec succès après 1+ réparation(s)
    failed: List[str] = field(default_factory=list)  # noms d'algos non réparés (filet de sécurité réactif)


def _build_repair_messages(algo_name: str, problem_title: str, type_name: str,
                            skeleton: str, error_detail: str) -> list:
    from shared.validation.skeleton_contract import ENVIRONMENT_CONTRACT

    return [{"role": "user", "content": (
        ENVIRONMENT_CONTRACT + "\n"
        f"Ce skeleton Python pour l'algorithme '{algo_name}' "
        f"(problème : {problem_title}, type de données : {type_name}) "
        f"échoue à l'exécution réelle sur le dataset généré, avec l'erreur suivante :\n\n"
        f"{error_detail}\n\n"
        f"Voici le code actuel :\n\n```python\n{skeleton}\n```\n\n"
        "Corrige le code pour que cette erreur précise ne se reproduise plus, en "
        "conservant l'intention algorithmique d'origine. Bibliothèques autorisées : "
        "numpy, pandas, scipy, sklearn, statsmodels, matplotlib (+ celles déjà "
        "importées si l'erreur ne vient pas d'un import interdit).\n"
        "Si l'erreur vient d'une colonne qui n'existe probablement pas dans les "
        "données réelles, reconstruis-la raisonnablement à partir des colonnes "
        "disponibles plutôt que de supposer qu'elle existe.\n"
        "Si l'erreur vient d'un timeout ou d'une boucle coûteuse, vectorise le "
        "calcul avec numpy/pandas.\n"
        "Assure-toi que `df['is_anomaly']` est bien assigné (valeurs 0/1) avec un "
        "taux de détection réaliste (ni 0%, ni la quasi-totalité des lignes) — "
        "utilise un seuil basé sur un percentile ou un z-score si le seuil d'origine "
        "n'est pas calibré à l'échelle réelle des données.\n"
        "Retourne UNIQUEMENT le code Python corrigé, sans balise markdown."
    )}]


def _repair_skeleton_once(algo_name: str, problem_title: str, type_name: str,
                           skeleton: str, error_detail: str) -> Optional[str]:
    """Un appel LLM de réparation. Retourne le nouveau skeleton (syntaxiquement
    valide) ou None si le LLM échoue ou renvoie du code invalide."""
    from shared.llm.router import chat_with_meta

    messages = _build_repair_messages(algo_name, problem_title, type_name, skeleton, error_detail)
    resp = chat_with_meta(messages, temperature=0.2, profile="code")
    new_skeleton = (resp.get("content") or "").strip()
    new_skeleton = new_skeleton.replace("```python", "").replace("```", "").strip()

    if not new_skeleton or len(new_skeleton) < 30:
        return None
    try:
        ast.parse(new_skeleton)
    except SyntaxError:
        return None
    return new_skeleton


def validate_and_repair_problem_algorithms(
    type_id: str,
    type_name: str,
    prob_id: int,
    prob_key: str,
    prob_title: str,
    df: pd.DataFrame,
    alg_repo,
) -> ExecutionValidationSummary:
    """
    Pour chaque algorithme déjà sauvé en base pour ce problème, valide le
    skeleton par exécution réelle sur `df` (le dataset qui vient d'être généré
    par S4), et répare via LLM en cas d'échec — jusqu'à MAX_EXECUTION_REPAIRS
    tentatives par algorithme.

    Met à jour `algorithms.python_skeleton` en base + Minio en cas de réparation
    réussie. N'échoue jamais bruyamment : les algos non réparables sont juste
    listés dans le résumé (ils resteront visibles via shared.quality --watch).
    """
    summary = ExecutionValidationSummary()

    if df is None or df.empty:
        return summary

    algos = alg_repo.find_algorithms(prob_id) or []
    if not algos:
        return summary

    for algo in algos:
        algo_id = algo.get("id")
        algo_name = algo.get("name", algo.get("algorithm_key", f"algo#{algo_id}"))
        skeleton = algo.get("python_skeleton", "")
        if not algo_id or not skeleton:
            continue

        ok, detail = validate_skeleton_execution(skeleton, df)
        if ok:
            summary.validated += 1
            continue

        log.warning("[S4][exec-validate] %s/%s/%s échoue à l'exécution réelle : %s",
                    type_name, prob_key, algo_name, detail)

        repaired_ok = False
        current_skeleton = skeleton
        current_detail = detail
        for attempt in range(1, MAX_EXECUTION_REPAIRS + 1):
            new_skeleton = _repair_skeleton_once(
                algo_name, prob_title, type_name, current_skeleton, current_detail)
            if not new_skeleton:
                log.warning("[S4][exec-validate] %s/%s/%s réparation LLM échouée (tentative %d/%d)",
                            type_name, prob_key, algo_name, attempt, MAX_EXECUTION_REPAIRS)
                continue

            new_ok, new_detail = validate_skeleton_execution(new_skeleton, df)
            if new_ok:
                _persist_repaired_skeleton(alg_repo, algo_id, new_skeleton,
                                            type_id, prob_key, algo.get("algorithm_key", ""))
                log.info("[S4][exec-validate] ✓ %s/%s/%s réparé (tentative %d) : %s",
                         type_name, prob_key, algo_name, attempt, new_detail)
                repaired_ok = True
                break

            current_skeleton = new_skeleton
            current_detail = new_detail
            log.warning("[S4][exec-validate] %s/%s/%s toujours invalide après réparation (tentative %d/%d) : %s",
                        type_name, prob_key, algo_name, attempt, MAX_EXECUTION_REPAIRS, new_detail)

        if repaired_ok:
            summary.repaired += 1
        else:
            summary.failed.append(algo_name)

    return summary


def _persist_repaired_skeleton(alg_repo, algo_id: int, new_skeleton: str,
                                type_id: str, prob_key: str, algo_key: str) -> None:
    """Sauve le skeleton réparé en MySQL + re-upload Minio (best-effort)."""
    try:
        alg_repo.update_skeleton(algo_id, new_skeleton)
    except Exception as exc:
        log.warning("[S4][exec-validate] échec sauvegarde MySQL skeleton réparé algo#%s : %s", algo_id, exc)
        return

    try:
        from shared.storage.catalogue_storage import upload_algorithm_skeleton
        upload_algorithm_skeleton(type_id, prob_key, algo_key, new_skeleton)
    except Exception as exc:
        log.warning("[S4][exec-validate] échec re-upload Minio skeleton réparé algo#%s : %s", algo_id, exc)
