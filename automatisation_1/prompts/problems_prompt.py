"""
Prompt S2 — Génération / complétion des problèmes de données de mobilité.

En mode enrichissement, le prompt passe de "génère 3 nouveaux problèmes différents"
à "complète la couverture : voici ce qui existe déjà, qu'est-ce qui manque encore ?"
"""
from __future__ import annotations


def build_prompt(
    type_name: str,
    description: str,
    specifications: str,
    existing_titles: list | None = None,
    existing_problems: list | None = None,
) -> list:
    """
    Construit le prompt pour la génération (ou complétion) des problèmes.

    Args:
        existing_problems : liste de dicts {title, description, problem_key/key} déjà en DB.
                            Quand fournie, active le mode "complétion" — le LLM identifie
                            les lacunes plutôt que de générer depuis zéro.
        existing_titles   : liste de titres (fallback si existing_problems absent).
    """
    system = (
        "Tu es un expert en systèmes de transport urbain, qualité des données de mobilité, "
        "et en analyse d'infrastructures de capteurs (boucles inductives, GPS, vidéo, "
        "billettique, Bluetooth, etc.). Tu génères des analyses précises, techniques et "
        "exploitables des problèmes de données rencontrés dans des contextes réels."
    )

    # ── Contexte de couverture existante ──────────────────────────────────────
    if existing_problems:
        n = len(existing_problems)
        lines = []
        for p in existing_problems:
            key  = p.get("problem_key") or p.get("key", "?")
            t    = p.get("title", "?")
            desc = str(p.get("description", "")).strip()
            brief = desc[:110] + "…" if len(desc) > 110 else desc
            lines.append(f"  [{key}] {t}\n        ↳ {brief}")
        coverage_block = (
            f"\n\nCOUVERTURE EXISTANTE — {n} problème(s) déjà documentés pour ce type :\n"
            + "\n".join(lines)
            + "\n\nTa mission : COMPLÉTER cette couverture. Identifie les problèmes importants "
            "qui manquent encore — ceux qui ne sont ni couverts ni implicitement inclus dans "
            "les descriptions ci-dessus. Génère exactement 3 problèmes NOUVEAUX et "
            "complémentaires."
        )
        task_verb = "Complète la couverture de ce type de données"
    elif existing_titles:
        titles_str = "\n".join(f"  • {t}" for t in existing_titles)
        coverage_block = (
            f"\n\nProblèmes déjà documentés (à NE PAS répéter) :\n{titles_str}\n\n"
            "Génère exactement 3 problèmes DIFFÉRENTS et complémentaires à ceux listés."
        )
        task_verb = "Génère des problèmes complémentaires pour ce type de données"
    else:
        coverage_block = ""
        task_verb = (
            "Analyse ce type de données de mobilité urbaine et génère exactement 3 problèmes "
            "distincts, concrets et techniques, tels qu'ils se manifestent dans des projets réels"
        )

    user = f"""{task_verb}.

TYPE DE DONNÉES : {type_name}
DESCRIPTION : {description}
SPÉCIFICATIONS : {specifications}{coverage_block}

Réponds UNIQUEMENT avec un objet JSON valide dans ce format exact :

{{
  "problems": [
    {{
      "key": "p1",
      "title": "Titre court et précis du problème (max 80 caractères)",
      "description": "Description détaillée : manifestations concrètes, conditions d'apparition, impact sur la donnée (3-5 phrases).",
      "causes": [
        "Cause technique précise 1 (équipement, protocole, environnement...)",
        "Cause technique précise 2",
        "Cause technique précise 3"
      ],
      "consequences": "Impact concret sur les analyses, décisions opérationnelles ou systèmes aval (2-3 phrases).",
      "frequency": "rare|occasionnel|courant|très courant",
      "impact_level": "faible|moyen|élevé|critique",
      "priority": 1,
      "affected_actors": [
        "Opérateurs de transport",
        "Planificateurs urbains",
        "Usagers"
      ],
      "detection_indicators": [
        "Indicateur mesurable 1 (ex: Taux de valeurs manquantes > 5% sur 1h)",
        "Indicateur mesurable 2 (ex: Écart-type > 3σ par rapport à la moyenne historique)"
      ],
      "solutions_overview": "Description en 2-3 phrases des familles d'approches algorithmiques pour résoudre ce problème (détection d'anomalies, imputation, filtrage...).",
      "data_requirements": "Données nécessaires pour détecter et traiter ce problème : historique minimum, granularité, variables requises."
    }},
    {{
      "key": "p2",
      "title": "...",
      "description": "...",
      "causes": ["...", "...", "..."],
      "consequences": "...",
      "frequency": "courant",
      "impact_level": "élevé",
      "priority": 2,
      "affected_actors": ["..."],
      "detection_indicators": ["...", "..."],
      "solutions_overview": "...",
      "data_requirements": "..."
    }},
    {{
      "key": "p3",
      "title": "...",
      "description": "...",
      "causes": ["...", "...", "..."],
      "consequences": "...",
      "frequency": "rare",
      "impact_level": "moyen",
      "priority": 3,
      "affected_actors": ["..."],
      "detection_indicators": ["...", "..."],
      "solutions_overview": "...",
      "data_requirements": "..."
    }}
  ]
}}

RÈGLES :
- Les 3 problèmes doivent être DISTINCTS (pas de redondance thématique)
- priority : 1=haute priorité, 2=moyenne, 3=basse
- Les causes doivent être spécifiques au matériel/protocole de ce type de donnée
- JSON valide uniquement, sans texte avant ou après"""

    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]


def build_repair_prompt(original: list, bad_response: str, error: str) -> list:
    return original + [
        {"role": "assistant", "content": bad_response},
        {
            "role": "user",
            "content": f"Erreur JSON : {error}\nCorrige et retourne UNIQUEMENT le JSON valide complet.",
        },
    ]
