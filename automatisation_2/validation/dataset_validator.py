"""
Validation du schéma de dataset retourné par le LLM.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger("auto2.dataset_validator")

REQUIRED_COLUMN_NAMES = {"timestamp", "sensor_id", "value", "anomaly_flag"}
VALID_DTYPES = {"datetime", "categorical", "int", "integer", "float", "number", "bool", "boolean", "string"}


class ValidationResult:
    def __init__(self, ok: bool, data: Optional[Dict] = None, errors: Optional[List[str]] = None):
        self.ok     = ok
        self.data   = data or {}
        self.errors = errors or []

    def __repr__(self) -> str:
        return f"ValidationResult(ok={self.ok}, errors={self.errors})"


def validate_dataset_schema(raw: Any) -> ValidationResult:
    """
    Valide et normalise le schéma retourné par le LLM.

    Le schéma attendu est un dict avec au moins : dataset_name, columns.
    Chaque colonne doit avoir : name, dtype, params.
    Les 4 colonnes obligatoires doivent être présentes.
    """
    errors: List[str] = []

    if not isinstance(raw, dict):
        return ValidationResult(False, errors=["Réponse n'est pas un dict JSON"])

    # ── dataset_name ──────────────────────────────────────────────────────────
    name = raw.get("dataset_name", "")
    if not name:
        name = "synthetic_dataset"
    name = re.sub(r"[^\w]", "_", str(name))[:60].strip("_").lower()
    if not name:
        name = "synthetic_dataset"

    # ── columns ───────────────────────────────────────────────────────────────
    columns = raw.get("columns", [])
    if not isinstance(columns, list) or len(columns) < 4:
        return ValidationResult(False, errors=["'columns' manquant ou < 4 éléments"])

    cleaned_cols: List[Dict] = []
    seen_names = set()

    for i, col in enumerate(columns):
        if not isinstance(col, dict):
            errors.append(f"Colonne {i} n'est pas un dict")
            continue

        col_name  = str(col.get("name", f"col_{i}")).strip()
        col_dtype = str(col.get("dtype", "float")).strip().lower()
        col_desc  = str(col.get("description", ""))
        col_params = col.get("params", {})

        if col_name in seen_names:
            errors.append(f"Colonne dupliquée : {col_name}")
            continue

        if col_dtype not in VALID_DTYPES:
            log.warning("[VALIDATOR] dtype inconnu '%s' → float", col_dtype)
            col_dtype = "float"

        if not isinstance(col_params, dict):
            col_params = {}

        # Correction des params datetime
        if col_dtype == "datetime":
            col_params.setdefault("start", "2023-01-01")
            col_params.setdefault("freq", "5min")
            col_params.setdefault("periods", raw.get("n_rows", 5000))

        # Correction des params float/int
        if col_dtype in ("float", "number", "int", "integer"):
            col_params.setdefault("mean", 50.0)
            col_params.setdefault("std", 15.0)
            col_params.setdefault("min", 0.0)
            col_params.setdefault("max", 1000.0)
            # Cohérence min/max
            try:
                vmin = float(col_params["min"])
                vmax = float(col_params["max"])
                if vmin >= vmax:
                    col_params["max"] = vmin + 100.0
            except (TypeError, ValueError):
                col_params["min"] = 0.0
                col_params["max"] = 1000.0

        # Correction bool
        if col_dtype in ("bool", "boolean"):
            prob = col_params.get("probability", 0.05)
            try:
                prob = float(prob)
                if not (0 < prob < 1):
                    prob = 0.05
            except (TypeError, ValueError):
                prob = 0.05
            col_params["probability"] = prob

        # Correction categorical
        if col_dtype == "categorical":
            values = col_params.get("values", ["A", "B", "C"])
            if not isinstance(values, list) or len(values) == 0:
                col_params["values"] = ["A", "B", "C"]
            weights = col_params.get("weights")
            if weights and isinstance(weights, list) and len(weights) != len(col_params["values"]):
                col_params.pop("weights", None)

        seen_names.add(col_name)
        cleaned_cols.append({
            "name":        col_name,
            "dtype":       col_dtype,
            "description": col_desc,
            "params":      col_params,
        })

    # Vérification des 4 colonnes obligatoires
    found_names = {c["name"] for c in cleaned_cols}
    missing = REQUIRED_COLUMN_NAMES - found_names
    if missing:
        errors.append(f"Colonnes obligatoires manquantes : {missing}")
        # Ajout minimal pour ne pas bloquer le pipeline
        _add_missing_columns(cleaned_cols, missing, raw.get("n_rows", 5000))
        found_names = {c["name"] for c in cleaned_cols}

    if errors:
        log.warning("[VALIDATOR] %d avertissements : %s", len(errors), errors)

    schema = {
        "dataset_name": name,
        "description":  raw.get("description", "Synthetic urban mobility dataset"),
        "n_rows":       int(raw.get("n_rows", 5000)),
        "columns":      cleaned_cols,
    }
    return ValidationResult(True, data=schema, errors=errors)


def _add_missing_columns(cols: List[Dict], missing: set, n_rows: int) -> None:
    defaults = {
        "timestamp": {
            "name": "timestamp", "dtype": "datetime", "description": "Timestamp",
            "params": {"start": "2023-01-01", "freq": "5min", "periods": n_rows},
        },
        "sensor_id": {
            "name": "sensor_id", "dtype": "categorical", "description": "Sensor ID",
            "params": {"values": [f"S{i:03d}" for i in range(1, 11)]},
        },
        "value": {
            "name": "value", "dtype": "float", "description": "Main measurement",
            "params": {"mean": 50.0, "std": 15.0, "min": 0.0, "max": 500.0},
        },
        "anomaly_flag": {
            "name": "anomaly_flag", "dtype": "bool", "description": "Ground-truth anomaly label",
            "params": {"probability": 0.05},
        },
    }
    for col_name in missing:
        if col_name in defaults:
            cols.insert(0, defaults[col_name])
