"""
Prompt LLM pour la génération du schéma de dataset synthétique.

Le LLM reçoit le contexte d'un type de données + problème ET les colonnes
attendues par les algorithmes déjà générés, pour que le dataset soit cohérent.
"""
from __future__ import annotations
from typing import List


_SYSTEM = (
    "You are a data scientist specializing in urban mobility systems. "
    "You design realistic synthetic datasets for anomaly detection research. "
    "Respond ONLY with a valid JSON object, no markdown, no explanation."
)

_TEMPLATE = """\
Context:
- Urban mobility data type: {type_name}
- Domain: {domain}
- Problem to study: {problem_title}
- Problem description: {problem_desc}
- Data challenges: {data_challenges}
{algo_context}
Design a synthetic dataset schema that:
1. Has a 'timestamp' datetime column (continuous time series, 1-minute frequency, start 2023-01-01)
2. Has an 'anomaly_flag' boolean column (5% anomaly rate — marks rows that SHOULD be anomalies)
3. Includes ALL the columns the algorithms above reference (mandatory — do NOT omit them)
4. Adds 2-3 extra contextual columns if they are realistic for {type_name}
5. Uses realistic value ranges for {type_name} urban mobility data

IMPORTANT rules:
- Column names must EXACTLY match what the algorithms reference
- For GPS data types: MUST include 'lat' (float, WGS84 latitude) and 'lon' (float, WGS84 longitude)
- Anomalies in GPS data = position jumps or duplicate coordinates (inject via anomaly_flag)
- For sensor/traffic data: MUST include 'value', 'sensor_id', etc. as expected by algorithms
- Every float/int column MUST have realistic mean, std, min, max for the data type

Return EXACTLY this JSON structure:
{{
  "dataset_name": "<snake_case_name_max_40_chars>",
  "description": "<one sentence describing the dataset>",
  "n_rows": 5000,
  "columns": [
    {{
      "name": "timestamp",
      "dtype": "datetime",
      "description": "Measurement timestamp",
      "params": {{"start": "2023-01-01", "freq": "1min", "periods": 5000}}
    }},
    {{
      "name": "anomaly_flag",
      "dtype": "bool",
      "description": "True if the row is a ground-truth anomaly",
      "params": {{"probability": 0.05}}
    }},
    ... (all required columns matching algorithm expectations)
  ]
}}

All numeric params (mean, std, min, max) MUST be realistic numbers for {type_name}.
"""


def _build_algo_context(algorithm_columns: List[str]) -> str:
    """Génère la section de contexte des colonnes attendues par les algorithmes."""
    if not algorithm_columns:
        return ""
    cols_str = ", ".join(f"'{c}'" for c in sorted(set(algorithm_columns)))
    return (
        f"\nALGORITHM COLUMN REQUIREMENTS (mandatory):\n"
        f"The algorithms generated for this problem reference these DataFrame columns: {cols_str}\n"
        f"Your dataset schema MUST include ALL of these columns with matching names.\n"
    )


def build_prompt(
    type_name: str,
    domain: str,
    problem_title: str,
    problem_desc: str,
    data_challenges: str = "",
    algorithm_columns: List[str] | None = None,
) -> tuple[str, str]:
    """
    Retourne (system_prompt, user_prompt) pour l'appel LLM.

    Args:
        algorithm_columns : colonnes référencées par les algorithmes (df['xxx']) pour ce problème.
                            Passées à None si pas encore connu.
    """
    algo_context = _build_algo_context(algorithm_columns or [])
    user = _TEMPLATE.format(
        type_name=type_name,
        domain=domain or "urban mobility",
        problem_title=problem_title,
        problem_desc=problem_desc,
        data_challenges=data_challenges or "variable quality sensor data",
        algo_context=algo_context,
    )
    return _SYSTEM, user
