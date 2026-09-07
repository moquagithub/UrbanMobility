"""
Prompt S1b — Enrichissement LLM des types de données.
Génère domaine, format, sources, cas d'usage, volume, normes, défis.
"""
from __future__ import annotations


def build_prompt(type_name: str, description: str, specifications: str) -> list:
    system = (
        "Tu es un expert en systèmes d'information de mobilité urbaine, "
        "en infrastructure de collecte de données de transport, "
        "et en normes d'interopérabilité (DATEX II, GTFS, NeTEx, GTFS-RT, etc.). "
        "Tu fournis des informations techniques précises et factuelles sur les types "
        "de données de mobilité utilisés par les collectivités et opérateurs de transport."
    )

    user = f"""Analyse ce type de données de mobilité urbaine et fournis une fiche technique enrichie.

TYPE DE DONNÉES : {type_name}
DESCRIPTION : {description}
SPÉCIFICATIONS : {specifications}

Réponds UNIQUEMENT avec un objet JSON valide dans ce format exact :

{{
  "domain": "Domaine principal parmi : Gestion du trafic routier | Transport en commun | Mobilité douce | Stationnement | Environnement et qualité de l'air | Multimodalité | Logistique urbaine",
  "data_format": "Format et mode de transmission : ex. 'Flux temps-réel MQTT + fichiers batch CSV quotidiens'",
  "is_real_time": true,
  "typical_volume": "Volume typique : ex. '2 millions de mesures/jour par boucle', '50 MB/heure par caméra'",
  "sources": [
    "Source concrète 1 (ex: Boucles inductives enterrées sous chaussée)",
    "Source concrète 2 (ex: Radar Doppler sur portique)",
    "Source concrète 3"
  ],
  "use_cases": [
    "Cas d'usage précis 1 (ex: Optimisation adaptative des cycles de feux tricolores)",
    "Cas d'usage précis 2 (ex: Estimation du temps de parcours temps-réel)",
    "Cas d'usage précis 3",
    "Cas d'usage précis 4"
  ],
  "standardization": "Normes et standards applicables : ex. 'DATEX II v3.3, ISO 14827 (NTCIP), APADS, EN ISO 20218'",
  "data_challenges": "Principaux défis techniques et opérationnels : biais, pannes, calibration, interopérabilité, RGPD... (3-4 phrases)"
}}

RÈGLES :
- JSON valide uniquement, pas de texte autour
- Sois précis et factuel, pas générique
- Les sources doivent être les équipements/systèmes réels qui collectent ces données
- Les cas d'usage doivent être concrets et opérationnels"""

    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]


def build_repair_prompt(original: list, bad_response: str, error: str) -> list:
    return original + [
        {"role": "assistant", "content": bad_response},
        {"role": "user", "content": f"JSON invalide : {error}\nRetourne UNIQUEMENT le JSON corrigé."},
    ]
