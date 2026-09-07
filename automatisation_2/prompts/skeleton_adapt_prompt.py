"""
Prompt d'adaptation de skeleton.

Objectif : prendre un skeleton Python généré sans connaître le dataset
et le réécrire pour qu'il utilise UNIQUEMENT les colonnes qui existent
réellement dans le DataFrame chargé par le notebook.

Le LLM reçoit :
  - Le nom et la description de l'algorithme
  - La liste des colonnes disponibles avec leurs types
  - Un échantillon de données (quelques lignes JSON)
  - Le skeleton original

Il retourne le skeleton adapté — même logique algorithmique, colonnes corrigées.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from shared.validation.skeleton_contract import ENVIRONMENT_CONTRACT


def build_adaptation_messages(
    algo_name: str,
    algo_description: str,
    problem_title: str,
    available_columns: List[Dict],  # [{"name": "lat", "dtype": "float64", ...}, ...]
    data_sample: str,               # JSON string des premières lignes du dataset
    original_skeleton: str,
    data_type_name: str = "",
) -> List[Dict]:
    """
    Construit les messages LLM pour adapter un skeleton aux colonnes réelles du dataset.

    Args:
        algo_name          : nom de l'algorithme
        algo_description   : description de l'algorithme
        problem_title      : titre du problème traité
        available_columns  : colonnes réelles du dataset [{name, dtype, description?}]
        data_sample        : aperçu des données (JSON, ~5 premières lignes)
        original_skeleton  : code Python original du skeleton
        data_type_name     : nom du type de données (ex: "Traces GPS")
    """
    col_lines = "\n".join(
        f"  - {c['name']} ({c.get('dtype', 'object')}) : {c.get('description', '')}"
        for c in available_columns
    )

    system_msg = (
        "Tu es un expert en traitement de données de mobilité urbaine et en Python scientifique. "
        "Tu adaptes des squelettes d'algorithmes pour qu'ils fonctionnent avec des datasets réels. "
        "Tu retournes UNIQUEMENT le code Python, sans commentaires, sans balises markdown."
    )

    user_msg = f"""## Contexte

Type de données : {data_type_name}
Problème traité : {problem_title}
Algorithme : {algo_name}
Description : {algo_description}

## Colonnes réellement disponibles dans le DataFrame `df`

{col_lines}

## Échantillon des données (5 premières lignes)

```json
{data_sample}
```

## Skeleton original (à adapter)

```python
{original_skeleton}
```

## Tâche

Le skeleton ci-dessus a été généré SANS connaître les colonnes exactes du dataset.
Il peut donc référencer des colonnes qui n'existent pas dans `df` (ex: `df['reference_time']`
alors que la colonne s'appelle `df['timestamp']`).

Adapte ce skeleton pour qu'il :
1. Utilise UNIQUEMENT les colonnes listées ci-dessus (ne pas inventer de colonnes)
2. Conserve exactement la même logique algorithmique de détection d'anomalies
3. Assigne obligatoirement `df['is_anomaly']` avec des valeurs 0 ou 1
4. Reste dans une fonction Python propre avec la signature `def run(df: pd.DataFrame) -> pd.DataFrame:`
5. Importe uniquement des bibliothèques disponibles (numpy, pandas, scipy, sklearn, statsmodels)

Si une colonne requise par l'algorithme n'existe pas, utilise la colonne disponible la plus proche
sémantiquement (ex: `timestamp` → `timestamp`, `latitude` → `lat`, `speed` → `speed_kmh`).

Retourne UNIQUEMENT le code Python adapté, sans aucun commentaire explicatif ni balise markdown.

{ENVIRONMENT_CONTRACT}"""

    return [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": user_msg},
    ]
