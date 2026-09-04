"""
services/normality_service.py
==============================
Logique de la page Streamlit `page_normality` (app_eda.py), portée en
service pur. `_run_normality_tests()` reprend fidèlement la fonction
interne `run_normality_tests()` de app_eda.py (déjà une fonction pure,
seulement décorée `@st.cache_data` côté Streamlit) : Shapiro-Wilk,
D'Agostino-Pearson, Anderson-Darling, verdict par consensus.

Le résultat par colonne est mis en cache dans le record du dataset
(`dataset_store`) pour éviter de relancer les 3 tests à chaque requête —
c'est l'équivalent du `@st.cache_data` original, mais persistant.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from app.services import dataset_store, eda_service
from app.services.json_safe import safe_float, safe_float_list

ALPHA = 0.05  # seuil de rejet, identique à app_eda.py


def _run_one_column(df: pd.DataFrame, col: str) -> Dict[str, Any]:
    s = df[col].dropna()
    n = len(s)

    if n < 3:
        return {
            "colonne": col, "n": n,
            "skewness": float(s.skew()) if n >= 2 else None,
            "kurtosis": float(s.kurtosis()) if n >= 2 else None,
            "sw_stat": None, "sw_p": None, "sw_normal": None,
            "dp_stat": None, "dp_p": None, "dp_normal": None,
            "ad_stat": None, "ad_sig": None, "ad_normal": None,
            "verdict": "insuffisant",
            "erreur": f"Seulement {n} valeur(s) — test impossible",
        }

    row: Dict[str, Any] = {
        "colonne": col, "n": n,
        "skewness": round(float(s.skew()), 4),
        "kurtosis": round(float(s.kurtosis()), 4),
        "erreur": None,
    }

    # Shapiro-Wilk (limité à 5000 obs, échantillonnage reproductible)
    try:
        sw_s, sw_p = scipy_stats.shapiro(s.sample(min(n, 5000), random_state=42))
        row["sw_stat"] = round(float(sw_s), 4)
        row["sw_p"] = round(float(sw_p), 6)
        row["sw_normal"] = sw_p >= ALPHA
    except Exception:
        row["sw_stat"] = row["sw_p"] = row["sw_normal"] = None

    # D'Agostino-Pearson (nécessite n≥8)
    if n >= 8:
        try:
            dp_s, dp_p = scipy_stats.normaltest(s)
            row["dp_stat"] = round(float(dp_s), 4)
            row["dp_p"] = round(float(dp_p), 6)
            row["dp_normal"] = dp_p >= ALPHA
        except Exception:
            row["dp_stat"] = row["dp_p"] = row["dp_normal"] = None
    else:
        row["dp_stat"] = row["dp_p"] = row["dp_normal"] = None

    # Anderson-Darling
    try:
        ad_res = scipy_stats.anderson(s, dist="norm")
        sig_idx = 2  # seuil 5%
        row["ad_stat"] = round(float(ad_res.statistic), 4)
        row["ad_sig"] = round(float(ad_res.significance_level[sig_idx]), 1)
        row["ad_normal"] = ad_res.statistic < ad_res.critical_values[sig_idx]
    except Exception:
        row["ad_stat"] = row["ad_sig"] = row["ad_normal"] = None

    votes_normal = sum(v for v in [row["sw_normal"], row["dp_normal"], row["ad_normal"]] if v is not None)
    votes_total = sum(1 for v in [row["sw_normal"], row["dp_normal"], row["ad_normal"]] if v is not None)
    if votes_total == 0:
        row["verdict"] = "insuffisant"
    elif votes_normal >= (votes_total / 2):
        row["verdict"] = "normale"
    else:
        row["verdict"] = "non_normale"

    for key in ("skewness", "kurtosis", "sw_stat", "sw_p", "dp_stat", "dp_p", "ad_stat", "ad_sig"):
        row[key] = safe_float(row.get(key))

    return row


def _get_or_compute_results(dataset_id: str) -> List[Dict[str, Any]]:
    record = eda_service.get_dataset(dataset_id)
    cached = record.get("normality_results")
    if cached is not None:
        return cached

    df = record["df"]
    num_cols = record["meta"]["colonnes_numeriques"]
    results = [_run_one_column(df, col) for col in num_cols]

    dataset_store.update(dataset_id, normality_results=results)
    return results


def get_normality_summary(dataset_id: str) -> Dict[str, Any]:
    """Équivalent du tableau récapitulatif de page_normality (toutes les variables numériques)."""
    results = _get_or_compute_results(dataset_id)

    n_normales = sum(1 for r in results if r["verdict"] == "normale")
    n_non_normales = sum(1 for r in results if r["verdict"] == "non_normale")
    n_insuffisantes = sum(1 for r in results if r["verdict"] == "insuffisant")

    return {
        "dataset_id": dataset_id,
        "alpha": ALPHA,
        "n_normales": n_normales,
        "n_non_normales": n_non_normales,
        "n_insuffisantes": n_insuffisantes,
        "resultats": results,
    }


def _suggest_transformation(skew: float, kurt: float, data_min: float) -> Optional[Dict[str, str]]:
    """Reprend la logique de suggestion de transformation de page_normality (app_eda.py)."""
    if skew > 1 and data_min >= 0:
        return {
            "methode": "log1p (données positives, forte asymétrie droite)",
            "code_exemple": "df['col_log'] = np.log1p(df['col'])",
        }
    if skew > 0.5:
        return {
            "methode": "sqrt (asymétrie droite modérée)",
            "code_exemple": "df['col_sqrt'] = np.sqrt(df['col'].clip(0))",
        }
    if skew < -1:
        return {
            "methode": "puissance (x²) (forte asymétrie gauche)",
            "code_exemple": "df['col_sq'] = df['col'] ** 2",
        }
    if abs(kurt) > 3:
        return {
            "methode": "Yeo-Johnson / Box-Cox (kurtosis élevé)",
            "code_exemple": (
                "from sklearn.preprocessing import PowerTransformer\n"
                "pt = PowerTransformer(method='yeo-johnson')\n"
                "df['col_pt'] = pt.fit_transform(df[['col']])"
            ),
        }
    return {
        "methode": "PowerTransformer Yeo-Johnson (cas général)",
        "code_exemple": (
            "from sklearn.preprocessing import PowerTransformer\n"
            "pt = PowerTransformer(method='yeo-johnson')\n"
            "df['col_pt'] = pt.fit_transform(df[['col']])"
        ),
    }


def get_normality_detail(dataset_id: str, column: str) -> Dict[str, Any]:
    """
    Équivalent du Q-Q plot + fiche détaillée de page_normality pour une variable :
    données du Q-Q plot (droite de Henry), histogramme vs courbe normale théorique,
    et suggestion de transformation si la variable n'est pas normale.
    """
    record = eda_service.get_dataset(dataset_id)
    if column not in record["meta"]["colonnes_numeriques"]:
        raise ValueError(f"'{column}' n'est pas une colonne numérique de ce dataset.")

    results = _get_or_compute_results(dataset_id)
    resultat = next((r for r in results if r["colonne"] == column), None)
    if resultat is None:
        raise ValueError(f"Aucun résultat de normalité pour '{column}'.")

    df = record["df"]
    s = df[column].dropna()

    qq_plot = None
    histogramme = None
    if len(s) >= 3:
        (osm, osr), (slope, intercept, _) = scipy_stats.probplot(s, dist="norm")
        line_x = [float(min(osm)), float(max(osm))]
        line_y = [float(slope * x + intercept) for x in line_x]
        qq_plot = {
            "quantiles_theoriques": safe_float_list(osm),
            "quantiles_observes":   safe_float_list(osr),
            "droite_x":             line_x,
            "droite_y":             line_y,
        }

        n_bins = min(len(s), 20)
        counts, bin_edges = np.histogram(s, bins=n_bins, density=True)
        x_range = np.linspace(s.min(), s.max(), 200)
        norm_pdf = scipy_stats.norm.pdf(x_range, s.mean(), s.std())
        histogramme = {
            "bin_edges": safe_float_list(bin_edges),
            "counts":    safe_float_list(counts),
            "densite":   True,
            "courbe_x":  safe_float_list(x_range),
            "courbe_y":  safe_float_list(norm_pdf),
        }

    transformation = None
    if resultat["verdict"] != "normale":
        transformation = _suggest_transformation(
            skew=resultat.get("skewness") or 0,
            kurt=resultat.get("kurtosis") or 0,
            data_min=float(s.min()) if not s.empty else 0.0,
        )

    return {
        "dataset_id": dataset_id,
        "resultat": resultat,
        "qq_plot": qq_plot,
        "histogramme": histogramme,
        "transformation_recommandee": transformation,
    }
