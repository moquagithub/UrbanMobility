"""
Générateur de données synthétiques à partir d'un schéma LLM.

Prend le schema_json retourné par le LLM et génère un DataFrame pandas réaliste :
- datetime : pd.date_range avec fréquence configurable
- categorical : np.random.choice avec poids optionnels
- int/float : distribution normale tronquée (clip min/max)
- bool : distribution de Bernoulli
- Injection d'anomalies contrôlée dans la colonne 'value'
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger("auto2.data_generator")


def generate_dataframe(
    schema: List[Dict],
    n_rows: int = 5000,
    seed: int = 42,
    anomaly_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Génère un DataFrame à partir d'un schéma de colonnes.

    Args:
        schema          : liste de dicts {name, dtype, description, params}
        n_rows          : nombre de lignes à générer
        seed            : graine aléatoire pour reproductibilité
        anomaly_columns : colonnes à corrompre aux lignes anomaly_flag=True (typiquement
                          les colonnes réellement lues par les algorithmes du problème —
                          ex: lat/lon pour un saut spatial, timestamp pour une dérive
                          d'horloge). Défaut : ['value'] si non fourni, pour compatibilité.

    Retourne un DataFrame pandas propre et trié par timestamp.
    """
    rng = np.random.default_rng(seed)
    data: Dict[str, np.ndarray] = {}

    for col in schema:
        name   = col.get("name", "col")
        dtype  = col.get("dtype", "float")
        params = col.get("params", {})

        try:
            data[name] = _generate_column(rng, dtype, params, n_rows)
        except Exception as exc:
            log.warning("[GENERATOR] Colonne '%s' erreur (%s) — rempli de None", name, exc)
            data[name] = np.full(n_rows, None)

    df = pd.DataFrame(data)

    # Trier par timestamp si présent
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    # Injecter des anomalies dans les colonnes pertinentes là où anomaly_flag=True.
    # Sans anomaly_columns, on corrompait TOUJOURS 'value' uniquement, quel que soit le
    # thème du problème (saut GPS, dérive d'horloge...) — les algorithmes ciblant
    # d'autres colonnes (lat/lon, timestamp) ne détectaient alors jamais rien de réel,
    # rendant F1/precision/recall structurellement non pertinents pour eux.
    if "anomaly_flag" in df.columns:
        df = _inject_anomalies(df, rng, target_columns=anomaly_columns)

    return df


def _generate_column(rng: np.random.Generator, dtype: str, params: Dict, n: int) -> np.ndarray:
    dtype = dtype.lower()

    if dtype == "datetime":
        start   = params.get("start", "2023-01-01")
        freq    = params.get("freq", "5min")
        periods = params.get("periods", n)
        idx = pd.date_range(start=start, periods=periods, freq=freq)
        if len(idx) > n:
            idx = idx[:n]
        elif len(idx) < n:
            extra = pd.date_range(start=idx[-1], periods=n - len(idx) + 1, freq=freq)[1:]
            idx = idx.append(extra)
        return idx.to_numpy()

    if dtype == "categorical":
        values  = params.get("values", ["A", "B", "C"])
        weights = params.get("weights", None)
        if weights and len(weights) == len(values):
            w = np.array(weights, dtype=float)
            w /= w.sum()
        else:
            w = None
        return rng.choice(values, size=n, p=w)

    if dtype in ("int", "integer"):
        mean = params.get("mean", 50.0)
        std  = params.get("std", 15.0)
        vmin = params.get("min", 0)
        vmax = params.get("max", 1000)
        vals = rng.normal(mean, std, n)
        return np.clip(vals, vmin, vmax).astype(int)

    if dtype in ("float", "number"):
        mean  = params.get("mean", 50.0)
        std   = params.get("std", 15.0)
        vmin  = params.get("min", 0.0)
        vmax  = params.get("max", 1000.0)
        vals  = rng.normal(mean, std, n)
        return np.clip(vals, vmin, vmax).astype(float).round(3)

    if dtype in ("bool", "boolean"):
        prob = float(params.get("probability", 0.05))
        return rng.random(n) < prob

    if dtype == "string":
        prefix = params.get("prefix", "ID")
        n_vals = params.get("n_unique", 5)
        pool   = [f"{prefix}_{i:03d}" for i in range(1, n_vals + 1)]
        return rng.choice(pool, size=n)

    # Fallback float
    return rng.normal(50.0, 10.0, n)


def _inject_anomalies(
    df: pd.DataFrame,
    rng: np.random.Generator,
    target_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Corrompt les colonnes cibles là où anomaly_flag=True.

    Les anomalies sont des spikes (×4-8), des chutes à 0, des valeurs négatives, ou un
    décalage de plusieurs écarts-types (utile pour des colonnes à plage bornée comme
    lat/lon où une valeur multiplicative n'a pas de sens physique).

    target_columns : colonnes à corrompre. Si None ou vide, retombe sur ['value']
    (comportement historique) pour compatibilité.
    """
    mask = df["anomaly_flag"].astype(bool)
    n_anomalies = int(mask.sum())
    if n_anomalies == 0:
        return df

    candidates = [c for c in (target_columns or []) if c in df.columns]
    numeric_cols  = [c for c in candidates if pd.api.types.is_numeric_dtype(df[c])]
    datetime_cols = [c for c in candidates if pd.api.types.is_datetime64_any_dtype(df[c])]
    if not numeric_cols and not datetime_cols and "value" in df.columns:
        numeric_cols = ["value"]
    if not numeric_cols and not datetime_cols:
        return df

    df = df.copy()
    indices = np.where(mask)[0]

    # Colonnes datetime (ex: 'timestamp' pour une dérive d'horloge) : decalage temporel
    # aleatoire aux lignes anomaly_flag=True, sans re-trier (garde l'alignement des lignes).
    # Amplitude limitee a 0.5-3x l'intervalle typique : un decalage plus grand (ex: 5-20x)
    # fait passer le point devant/derriere ses voisins une fois re-trie par timestamp
    # (comme le font la plupart des algos), ce qui casse toute reconstruction de
    # reference basee sur la position/l'ordre plutot que sur une vraie derive locale.
    for col in datetime_cols:
        ts = df[col].to_numpy().copy()
        typical_step = pd.Series(df[col]).diff().dt.total_seconds().abs().median()
        typical_step = typical_step if typical_step and typical_step > 0 else 60.0
        offsets_sec = rng.uniform(0.5, 3.0, size=n_anomalies) * typical_step * rng.choice([-1, 1], size=n_anomalies)
        ts[indices] = ts[indices] + (offsets_sec * 1e9).astype("timedelta64[ns]")
        df[col] = ts

    for col in numeric_cols:
        col_vals = df[col].to_numpy(dtype=float).copy()
        col_std  = float(np.std(col_vals)) or 1.0
        col_max  = float(col_vals.max()) if col_vals.max() > 0 else 100.0
        anomaly_types = rng.choice(["spike", "zero", "negative", "offset"], size=n_anomalies)

        for idx, atype in zip(indices, anomaly_types):
            if atype == "spike":
                col_vals[idx] = col_vals[idx] * rng.uniform(4, 8)
            elif atype == "zero":
                col_vals[idx] = 0.0
            elif atype == "negative":
                col_vals[idx] = -col_vals[idx] * rng.uniform(0.5, 2.0)
            else:  # offset — decalage de plusieurs ecarts-types, pertinent pour
                    # les colonnes a plage bornee (lat/lon) ou une valeur
                    # multiplicative/nulle/negative n'a pas de sens physique
                col_vals[idx] += rng.choice([-1, 1]) * rng.uniform(5, 10) * col_std

        if col == "value":
            col_vals = np.clip(col_vals, None, col_max * 10)
        df[col] = col_vals

    return df


def compute_stats(df: pd.DataFrame) -> Dict:
    """Calcule les statistiques descriptives par colonne numérique."""
    stats = {}
    for col in df.select_dtypes(include=np.number).columns:
        s = df[col].describe()
        stats[col] = {
            "count": int(s["count"]),
            "mean":  round(float(s["mean"]), 4),
            "std":   round(float(s["std"]),  4),
            "min":   round(float(s["min"]),  4),
            "25%":   round(float(s["25%"]),  4),
            "50%":   round(float(s["50%"]),  4),
            "75%":   round(float(s["75%"]),  4),
            "max":   round(float(s["max"]),  4),
            "missing": int(df[col].isna().sum()),
        }
    return stats


def df_to_json_str(df: pd.DataFrame) -> str:
    """Convertit un DataFrame en JSON compact pour stockage MySQL."""
    df2 = df.copy()
    for col in df2.select_dtypes(include=["datetime64[ns]", "datetime64[ns, UTC]"]).columns:
        df2[col] = df2[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
    for col in df2.select_dtypes(include=["bool"]).columns:
        df2[col] = df2[col].astype(int)
    return df2.to_json(orient="records", date_format="iso", force_ascii=False)
