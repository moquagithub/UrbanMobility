"""
Validation du squelette Python d'un algorithme généré par LLM.

Vérifie :
1. Syntaxe Python valide (ast.parse)
2. Au moins une fonction définie
3. La fonction accepte un DataFrame (paramètre df)
4. La fonction assigne df['is_anomaly'] — OBLIGATOIRE pour la détection
5. Les bibliothèques utilisées sont raisonnables (pas tensorflow/torch)

En cas d'échec, retourne un message de feedback précis pour le LLM.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List, Optional


# Bibliothèques non disponibles dans l'environnement d'exécution
_FORBIDDEN_IMPORTS = {
    "tensorflow", "torch", "keras", "theano", "mxnet", "jax",
    "pymc", "pymc3", "gpflow", "osmnx", "node2vec", "ruptures",
    "pyspark", "dask", "ray",
    "cvxpy", "dtw",
}

# Bibliothèques lourdes/rares — avertissement mais pas bloquant
_HEAVY_IMPORTS = {"GPy", "stan", "rstan", "lightgbm", "xgboost", "catboost"}


@dataclass
class SkeletonValidation:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    feedback: str = ""  # message précis pour le LLM de réparation

    def __bool__(self) -> bool:
        return self.ok


def validate_skeleton(skeleton: str) -> SkeletonValidation:
    """
    Valide un squelette Python d'algorithme.

    Returns SkeletonValidation avec .ok=False et .feedback si invalide.
    """
    if not skeleton or not skeleton.strip():
        return SkeletonValidation(
            ok=False,
            errors=["python_skeleton est vide"],
            feedback="Le champ python_skeleton est vide. Tu dois fournir un squelette Python complet.",
        )

    errors: List[str] = []
    warnings: List[str] = []

    # ── 1. Syntaxe Python ────────────────────────────────────────────────
    try:
        tree = ast.parse(skeleton)
    except SyntaxError as exc:
        return SkeletonValidation(
            ok=False,
            errors=[f"Erreur de syntaxe Python : {exc}"],
            feedback=(
                f"Ton python_skeleton contient une erreur de syntaxe Python : {exc}. "
                "Corrige la syntaxe et retourne un JSON valide."
            ),
        )

    # ── 2. Au moins une fonction définie ────────────────────────────────
    func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    if not func_defs:
        errors.append("Aucune fonction Python définie (def ...)")
        return SkeletonValidation(
            ok=False,
            errors=errors,
            feedback=(
                "Ton python_skeleton ne définit aucune fonction Python (def ...). "
                "Tu dois définir une fonction principale qui prend `df: pd.DataFrame` en paramètre "
                "et retourne un DataFrame avec les colonnes `is_anomaly` et `anomaly_score`."
            ),
        )

    main_func = func_defs[0]

    # ── 3. Paramètre df ──────────────────────────────────────────────────
    args = [a.arg for a in main_func.args.args]
    if "df" not in args and not any("df" in a for a in args):
        warnings.append("La fonction ne semble pas accepter un paramètre `df`")

    # ── 4. is_anomaly assigné ────────────────────────────────────────────
    has_is_anomaly = bool(
        re.search(r"['\"]is_anomaly['\"]", skeleton) or
        re.search(r"is_anomaly\s*=", skeleton)
    )
    if not has_is_anomaly:
        errors.append("Le squelette ne définit pas df['is_anomaly']")
        return SkeletonValidation(
            ok=False,
            errors=errors,
            feedback=(
                "Ton python_skeleton ne contient pas d'assignation de `df['is_anomaly']`. "
                "C'est OBLIGATOIRE : la fonction DOIT détecter les anomalies et assigner "
                "`df['is_anomaly'] = True` pour les lignes anormales détectées, "
                "`False` pour les lignes normales. "
                "Elle doit aussi assigner `df['anomaly_score']` (float, 0.0-1.0). "
                "Exemple minimal :\n"
                "  df['anomaly_score'] = scores  # float array\n"
                "  df['anomaly_score'] = (df['anomaly_score'] - df['anomaly_score'].min()) / (df['anomaly_score'].max() - df['anomaly_score'].min() + 1e-9)\n"
                "  df['is_anomaly'] = df['anomaly_score'] > threshold\n"
                "Retourne le JSON corrigé avec ce mécanisme de détection."
            ),
        )

    # ── 5. Imports interdits ─────────────────────────────────────────────
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            else:
                names = [node.module.split(".")[0]] if node.module else []
            for name in names:
                if name in _FORBIDDEN_IMPORTS:
                    errors.append(f"Import interdit : {name} (non disponible dans l'environnement)")
                    return SkeletonValidation(
                        ok=False,
                        errors=errors,
                        feedback=(
                            f"Ton python_skeleton importe `{name}` qui n'est pas disponible "
                            "dans l'environnement d'exécution. "
                            "Utilise uniquement : numpy, pandas, scipy, sklearn, statsmodels, "
                            "matplotlib, pykalman, hmmlearn, filterpy, networkx, PyWavelets. "
                            "Réécris l'algorithme sans `{name}`."
                        ),
                    )
                if name in _HEAVY_IMPORTS:
                    warnings.append(f"Import lourd/rare : {name} — peut échouer à l'exécution")

    # ── 6. Return statement ──────────────────────────────────────────────
    has_return = bool(re.search(r"\breturn\s+df\b", skeleton))
    if not has_return:
        warnings.append("La fonction ne semble pas retourner df explicitement")

    if errors:
        feedback = "Erreurs dans ton python_skeleton :\n" + "\n".join(f"- {e}" for e in errors)
        return SkeletonValidation(ok=False, errors=errors, warnings=warnings, feedback=feedback)

    return SkeletonValidation(ok=True, warnings=warnings)


def build_skeleton_repair_messages(
    original_messages: list,
    skeleton: str,
    validation: SkeletonValidation,
) -> list:
    """
    Construit les messages de réparation pour le LLM.
    Inclut le squelette défaillant + le feedback précis.
    """
    return original_messages + [
        {
            "role": "user",
            "content": (
                f"PROBLÈME DÉTECTÉ dans le python_skeleton généré :\n\n"
                f"```python\n{skeleton[:800]}\n```\n\n"
                f"ERREUR : {validation.feedback}\n\n"
                "Retourne le même JSON que précédemment MAIS avec un python_skeleton corrigé. "
                "Le JSON doit contenir tous les champs requis (name, principle, math_formulation, "
                "python_skeleton, etc.)."
            ),
        }
    ]
