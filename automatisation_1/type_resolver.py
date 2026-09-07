"""
automatisation_1.type_resolver — Résolution non ambiguë du filtre --type.

Avec plusieurs dizaines de types partageant des préfixes ("Capteurs Bluetooth",
"Capteurs comptage routiers", "Capteurs environnementaux", "GPS temps réel" vs
"Traces GPS"...), une recherche par simple sous-chaîne peut faire correspondre
--type à PLUSIEURS types à la fois et les traiter tous silencieusement, alors
que l'appelant veut en traiter un seul et précis.
"""
from __future__ import annotations

from typing import Dict, List, Optional


class AmbiguousTypeFilter(ValueError):
    """Levée quand --type correspond à plusieurs types de données à la fois."""


def resolve_type_filter(
    data_types: List[Dict],
    type_filter: Optional[str],
) -> List[Dict]:
    """
    Résout --type vers EXACTEMENT un type (ou renvoie data_types tel quel si
    type_filter est vide).

    Ordre de résolution : id exact > nom exact > sous-chaîne UNIQUE.
    Si la sous-chaîne correspond à plusieurs types : AmbiguousTypeFilter.
    """
    if not type_filter:
        return data_types

    tf = type_filter.strip().lower()

    exact_id = [dt for dt in data_types if dt.get("id", "").lower() == tf]
    if exact_id:
        return exact_id[:1]

    exact_name = [dt for dt in data_types if dt.get("name", "").lower() == tf]
    if exact_name:
        return exact_name[:1]

    partial = [
        dt for dt in data_types
        if tf in dt.get("id", "").lower() or tf in dt.get("name", "").lower()
    ]
    if len(partial) > 1:
        candidates = ", ".join(f"{dt.get('id')} ({dt.get('name')})" for dt in partial)
        raise AmbiguousTypeFilter(
            f"--type '{type_filter}' correspond à {len(partial)} types : {candidates}. "
            "Précisez l'id exact (voir `python -m automatisation_1 status`) pour "
            "ne traiter qu'un seul type."
        )
    return partial
