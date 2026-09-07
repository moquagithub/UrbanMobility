"""
Prompt S3 — Génération / complétion d'un algorithme pour un problème de mobilité.

En mode enrichissement, le prompt passe de "génère un algo différent des X existants"
à "identifie la famille algorithmique manquante et génère un algo de cette famille".

Chaque appel génère un algorithme avec :
- Formulation mathématique LaTeX
- Pseudo-code
- Format entrée/sortie
- Hyperparamètres
- Librairies Python
- Métriques d'évaluation
- Exemple urbain concret
- Squelette Python fonctionnel (compact)
- Références
"""
from __future__ import annotations
from typing import List, Optional


def build_prompt(
    type_name: str,
    problem_title: str,
    problem_description: str,
    problem_causes: List[str],
    algorithm_index: int = 1,
    existing_algorithm_names: List[str] = None,
    existing_algorithms: List[dict] = None,
    algorithm_category_hint: str = "",
) -> list:
    """
    Génère le prompt pour UN SEUL algorithme.

    Args:
        existing_algorithms  : liste de dicts {name, category} déjà en DB.
                               Quand fournie, active le mode "complétion" — le LLM identifie
                               les familles non encore couvertes plutôt que de simplement éviter
                               des noms.
        existing_algorithm_names : noms bruts (fallback / dedup).
        algorithm_category_hint  : ignoré quand existing_algorithms est fourni.
    """
    system = (
        "Tu es un expert en traitement du signal, machine learning et algorithmique "
        "appliqués aux données de transport urbain. Tu fournis des algorithmes concrets, "
        "mathématiquement rigoureux, directement implémentables en Python. "
        "Tu réponds UNIQUEMENT en JSON valide, sans aucun texte autour."
    )

    # ── Contexte de couverture algorithmique ──────────────────────────────────
    if existing_algorithms:
        # Regroupe par famille
        by_family: dict[str, list[str]] = {}
        for a in existing_algorithms:
            cat  = a.get("category", "Autre")
            name = a.get("name", "?")
            by_family.setdefault(cat, []).append(name)

        family_lines = "\n".join(
            f"  • {cat} : {', '.join(names)}"
            for cat, names in by_family.items()
        )
        coverage = (
            "\n\nCOUVERTURE ALGORITHMIQUE EXISTANTE :\n"
            + family_lines
            + "\n\nTa mission : identifier une famille algorithmique importante NON encore couverte "
            "et générer un algorithme de cette famille. Privilégie les approches qui apportent "
            "une perspective différente des familles déjà représentées ci-dessus."
        )
        task_intro = "Complète la couverture algorithmique de ce problème"
    elif existing_algorithm_names:
        names_str = ", ".join(f'"{n}"' for n in existing_algorithm_names)
        coverage = (
            f"\n\nAlgorithmes déjà générés : {names_str}. "
            "Propose une famille algorithmique DIFFÉRENTE."
        )
        task_intro = f"Génère UN algorithme (n°{algorithm_index})"
    else:
        coverage = ""
        task_intro = f"Génère UN algorithme (n°{algorithm_index}/3)"

    # Hint de catégorie (ignoré si on a déjà la couverture complète)
    category_hint = ""
    if algorithm_category_hint and not existing_algorithms:
        category_hint = f"\n\nFamille suggérée pour cet algorithme : {algorithm_category_hint}"

    causes_str = "\n".join(f"  - {c}" for c in (problem_causes or []))

    user = f"""{task_intro} pour résoudre le problème suivant.

TYPE DE DONNÉES : {type_name}
PROBLÈME : {problem_title}
DESCRIPTION : {problem_description}
CAUSES :
{causes_str}{coverage}{category_hint}

Réponds UNIQUEMENT avec ce JSON (un seul algorithme) :

{{
  "key": "alg{algorithm_index}",
  "name": "Nom précis et distinctif de l'algorithme",
  "category": "Famille : Statistique | Machine Learning | Signal processing | Règles métier | Optimisation",
  "principle": "Principe en 3-4 phrases : comment il détecte/corrige le problème.",
  "complexity_time": "O(n log n)",
  "complexity_space": "O(k)",
  "advantages": ["Avantage concret 1", "Avantage concret 2"],
  "limitations": ["Limitation 1", "Limitation 2"],
  "math_formulation": "Formule LaTeX principale. Ex: $\\\\hat{{y}}_t = \\\\mu_w \\\\pm k\\\\sigma_w$",
  "pseudocode": "1. Étape 1\\n2. Étape 2\\n   2a. Sous-étape\\n3. Retourner résultat",
  "input_format": "DataFrame pandas [timestamp, sensor_id, value, ...] — granularité requise",
  "output_format": "DataFrame avec [anomaly_score: float, is_anomaly: bool, corrected_value: float]",
  "hyperparameters": [
    {{"name": "window_size", "type": "int", "default": 24, "range": "[6, 336]", "description": "Taille fenêtre glissante"}},
    {{"name": "threshold", "type": "float", "default": 3.0, "range": "[1.5, 5.0]", "description": "Seuil de détection"}}
  ],
  "required_libraries": ["numpy>=1.24", "pandas>=2.0", "scipy>=1.11"],
  "evaluation_metrics": [
    {{"name": "F1-Score", "formula": "$F_1 = 2PR/(P+R)$", "interpretation": "> 0.85 = excellent"}},
    {{"name": "FPR", "formula": "$FP/(FP+TN)$", "interpretation": "< 5% en production"}}
  ],
  "use_case_example": "Exemple avec ville et chiffres : appliqué à [lieu], détecté [N] anomalies sur [période] avec [métrique]=[valeur].",
  "python_skeleton": "import numpy as np\\nimport pandas as pd\\n\\ndef run(df: pd.DataFrame, window: int = 24, k: float = 3.0) -> pd.DataFrame:\\n    df = df.copy().sort_values('timestamp')\\n    vals = df['value'].to_numpy(float)\\n    scores = np.zeros(len(vals))\\n    for i in range(window, len(vals)):\\n        w = vals[i-window:i]\\n        mu, sigma = w.mean(), w.std()\\n        if sigma > 0: scores[i] = abs(vals[i] - mu) / sigma\\n    df['anomaly_score'] = scores / k\\n    df['is_anomaly'] = scores > k\\n    df['corrected_value'] = np.where(df['is_anomaly'], np.nan, vals)\\n    df['corrected_value'] = df['corrected_value'].interpolate()\\n    return df",
  "references": [
    {{"title": "Titre article ou livre", "authors": "Auteurs", "year": 2021, "venue": "Journal/Conférence", "doi": "10.xxxx/xxxxx"}}
  ]
}}

RÈGLES :
- UN SEUL objet JSON (pas de liste "algorithms")
- python_skeleton : code compact 12-18 lignes avec \\n pour les sauts de ligne
- math_formulation : LaTeX avec doubles backslashes pour les commandes
- JSON VALIDE uniquement — terminer toutes les chaînes"""

    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]


# Catégories suggérées pour diversifier les 3 algorithmes d'un même problème
CATEGORY_HINTS = [
    "Approche statistique classique (Z-Score, IQR, CUSUM, Kalman...)",
    "Machine Learning non supervisé (Isolation Forest, DBSCAN, Autoencoder, LOF...)",
    "Traitement du signal ou Deep Learning (LSTM, Wavelet, FFT, Prophet...)",
]


def build_repair_prompt(original_messages: list, bad_response: str, error_detail: str) -> list:
    return original_messages + [
        {"role": "assistant", "content": bad_response},
        {
            "role": "user",
            "content": (
                f"JSON invalide : {error_detail}\n"
                "Retourne UNIQUEMENT le JSON corrigé (un seul objet, pas de liste)."
            ),
        },
    ]
