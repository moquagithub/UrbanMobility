"""
services/recommendations_service.py
====================================
Logique de la page Streamlit `page_recommendations` (app_eda.py) +
son moteur `apply_transformations()`, portée en service pur.

Décision de conception (DQE-7) — transformations = NOUVEAU dataset
-------------------------------------------------------------------
Dans app_eda.py, "Appliquer les transformations" mute la session Streamlit
en place (un seul utilisateur, un seul dataset actif à la fois) : le
dataset transformé remplace l'original dans `st.session_state`, et
l'original n'est récupérable qu'en le ré-uploadant.

L'API expose plusieurs datasets en parallèle (multi-utilisateurs,
multi-onglets). `apply_recommendations()` crée donc un **nouveau**
dataset_id pour le résultat transformé, sans toucher au dataset d'origine.
Ce choix est cohérent avec le reste du code legacy : `eda_analyse.py`
fournit déjà `build_latex_summary(csv_path_before, csv_path_after, ...)` et
les figures `fig_compare_*_png(df_before, df_after, ...)`, conçues autour
de deux jeux de données distincts comparés côte à côte — exactement le
scénario "dataset original" + "dataset dérivé" (voir `report_service.py`,
qui utilise ces deux dataset_id pour le rapport de synthèse).
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from app.services import eda_service

PIPELINE_SUGGERE = [
    {"etape": "1", "action": "Suppression des doublons", "outil_suggere": "df.drop_duplicates()"},
    {"etape": "2", "action": "Traitement des valeurs manquantes", "outil_suggere": "SimpleImputer / MICE"},
    {"etape": "3", "action": "Traitement des outliers", "outil_suggere": "Winsorisation / IQR"},
    {"etape": "4", "action": "Transformation des distributions", "outil_suggere": "log1p / Box-Cox / PowerTransformer"},
    {"etape": "5", "action": "Encodage des variables catégorielles", "outil_suggere": "get_dummies / OrdinalEncoder"},
    {"etape": "6", "action": "Normalisation / Standardisation", "outil_suggere": "StandardScaler / MinMaxScaler"},
    {"etape": "7", "action": "Réduction de dimensionnalité", "outil_suggere": "PCA / SelectKBest"},
    {"etape": "8", "action": "Séparation train / test", "outil_suggere": "train_test_split(test_size=0.2)"},
]


def _build_context(df: pd.DataFrame, meta: Dict[str, Any], imp_df: pd.DataFrame) -> Dict[str, Any]:
    """Reprend fidèlement les calculs préliminaires de page_recommendations()."""
    num_cols = meta["colonnes_numeriques"]
    cat_cols = meta["colonnes_texte"]

    cols_high_missing = [c for c in df.columns if meta["colonnes"][c]["taux_completude"] < 80]
    cols_skewed = [c for c in num_cols if abs(meta["colonnes"][c].get("skewness", 0) or 0) > 1]
    cols_low_imp = list(imp_df[imp_df["score_importance"] < 25].index)

    corr_pairs: List[tuple] = []
    if len(num_cols) >= 2:
        corr = df[num_cols].corr().abs()
        for i in range(len(num_cols)):
            for j in range(i + 1, len(num_cols)):
                r = corr.iloc[i, j]
                if not np.isnan(r) and r >= 0.7:
                    corr_pairs.append((num_cols[i], num_cols[j], round(float(r), 3)))

    cols_non_normal = []
    for col in num_cols:
        s = df[col].dropna()
        if len(s) >= 8:
            try:
                _, p = scipy_stats.normaltest(s)
                if p < 0.05:
                    cols_non_normal.append(col)
            except Exception:
                pass

    cols_critical = [c for c in df.columns if meta["colonnes"][c]["taux_completude"] < 50]
    cols_moderate = [c for c in df.columns if 50 <= meta["colonnes"][c]["taux_completude"] < 80]

    return {
        "num_cols": num_cols, "cat_cols": cat_cols,
        "cols_high_missing": cols_high_missing, "cols_skewed": cols_skewed,
        "cols_low_imp": cols_low_imp, "corr_pairs": corr_pairs,
        "cols_non_normal": cols_non_normal,
        "cols_critical": cols_critical, "cols_moderate": cols_moderate,
    }


def _build_actions_catalog(df: pd.DataFrame, meta: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reprend fidèlement le catalogue d'actions de page_recommendations() (mêmes id/seuils/messages)."""
    reverse_map = {v: k for k, v in meta["mapping"].items()}
    doublons = meta["doublons"]
    actions: List[Dict[str, Any]] = []

    if doublons > 0:
        actions.append({
            "id": "drop_duplicates", "priorite": "haute", "categorie": "Qualité",
            "label": f"Supprimer les {doublons} doublons",
            "justification": "Les doublons faussent statistiques et modèles.",
            "code": "df = df.drop_duplicates()", "impact": "Élevé", "applicable": True,
        })
    if context["cols_critical"]:
        names = ", ".join([f"{c} ({reverse_map.get(c, c)})" for c in context["cols_critical"][:4]])
        actions.append({
            "id": "drop_critical_missing", "priorite": "haute", "categorie": "Valeurs manquantes",
            "label": f"Supprimer {len(context['cols_critical'])} variable(s) avec >50% manquants",
            "justification": f"Variables : {names}.",
            "code": f"df = df.drop(columns={context['cols_critical']})", "impact": "Élevé", "applicable": True,
        })
    if context["cols_moderate"]:
        names = ", ".join(context["cols_moderate"][:4])
        actions.append({
            "id": "impute_moderate_missing", "priorite": "moyenne", "categorie": "Valeurs manquantes",
            "label": f"Imputer {len(context['cols_moderate'])} variable(s) avec 20-50% manquants",
            "justification": f"Médiane (num.) / mode (cat.) pour : {names}.",
            "code": "df[col].fillna(df[col].median())  # pour chaque variable numérique",
            "impact": "Moyen", "applicable": True,
        })
    if context["cols_non_normal"]:
        names = ", ".join(context["cols_non_normal"][:4])
        actions.append({
            "id": "normalize_yeo", "priorite": "moyenne", "categorie": "Normalité",
            "label": f"Appliquer Yeo-Johnson sur {len(context['cols_non_normal'])} variable(s) non normales",
            "justification": f"Variables : {names}.",
            "code": "PowerTransformer(method='yeo-johnson').fit_transform(df[cols])",
            "impact": "Moyen", "applicable": True,
        })
    if context["cols_skewed"]:
        names = ", ".join(context["cols_skewed"][:4])
        actions.append({
            "id": "transform_skewed", "priorite": "moyenne", "categorie": "Distribution",
            "label": f"Appliquer log1p sur {len(context['cols_skewed'])} variable(s) asymétrique(s)",
            "justification": f"|skewness|>1 pour : {names}.",
            "code": "df[col] = np.log1p(df[col])", "impact": "Moyen", "applicable": True,
        })
    if context["cols_low_imp"]:
        names = ", ".join([f"{c} ({reverse_map.get(c, c)})" for c in context["cols_low_imp"][:4]])
        actions.append({
            "id": "drop_low_importance", "priorite": "basse", "categorie": "Sélection",
            "label": f"Supprimer {len(context['cols_low_imp'])} variable(s) peu importantes (score<25%)",
            "justification": f"Variables : {names}.",
            "code": f"df = df.drop(columns={context['cols_low_imp']})", "impact": "Faible", "applicable": True,
        })
    if context["cat_cols"]:
        actions.append({
            "id": "encode_categorical", "priorite": "basse", "categorie": "Encodage",
            "label": f"One-Hot Encoding des {len(context['cat_cols'])} variable(s) catégorielles",
            "justification": "Nécessaire pour les algorithmes ML.",
            "code": "df = pd.get_dummies(df, columns=cat_cols)", "impact": "Faible", "applicable": True,
        })
    if context["num_cols"]:
        actions.append({
            "id": "standardize", "priorite": "basse", "categorie": "Normalisation",
            "label": f"Standardiser (Z-score) les {len(context['num_cols'])} variables numériques",
            "justification": "Indispensable pour SVM, KNN, régression régularisée.",
            "code": "StandardScaler().fit_transform(df[num_cols])", "impact": "Faible", "applicable": True,
        })

    return actions


def get_recommendations(dataset_id: str) -> Dict[str, Any]:
    record = eda_service.get_dataset(dataset_id)
    df, meta, imp_df = record["df"], record["meta"], record["imp_df"]

    context = _build_context(df, meta, imp_df)
    actions = _build_actions_catalog(df, meta, context)

    missing_pct = 100 - meta["taux_completude_global"]
    kpis = {
        "problemes_critiques": sum([missing_pct > 20, meta["doublons"] > 0, len(context["cols_skewed"]) > 0]),
        "variables_a_traiter": len(context["cols_high_missing"]) + len(context["cols_low_imp"]),
        "paires_redondantes": len(context["corr_pairs"]),
        "variables_non_normales": len(context["cols_non_normal"]),
    }

    return {
        "dataset_id": dataset_id,
        "kpis": kpis,
        "actions": actions,
        "pipeline_suggere": PIPELINE_SUGGERE,
    }


def _apply_transformations(df: pd.DataFrame, selected_actions: List[str], context: Dict[str, Any]):
    """Port fidèle de apply_transformations() (app_eda.py) — même id d'actions, même comportement."""
    df_out = df.copy()
    journal: List[str] = []

    for action_id in selected_actions:
        if action_id == "drop_duplicates":
            before = len(df_out)
            df_out = df_out.drop_duplicates()
            removed = before - len(df_out)
            journal.append(f"Doublons supprimés : {removed} ligne(s) retirée(s).")

        elif action_id == "drop_critical_missing":
            cols = context["cols_critical"]
            df_out = df_out.drop(columns=[c for c in cols if c in df_out.columns])
            journal.append(f"Variables >50% manquants supprimées : {cols}")

        elif action_id == "impute_moderate_missing":
            cols = context["cols_moderate"]
            for col in cols:
                if col not in df_out.columns:
                    continue
                if pd.api.types.is_numeric_dtype(df_out[col]):
                    fill = df_out[col].median()
                    df_out[col] = df_out[col].fillna(fill)
                    journal.append(f"Imputation médiane → {col} (valeur={fill:.4f})")
                else:
                    fill = df_out[col].mode().iloc[0] if not df_out[col].dropna().empty else "INCONNU"
                    df_out[col] = df_out[col].fillna(fill)
                    journal.append(f"Imputation mode → {col} (valeur='{fill}')")

        elif action_id == "normalize_yeo":
            from sklearn.preprocessing import PowerTransformer
            cols = [c for c in context["cols_non_normal"] if c in df_out.columns]
            valid = [c for c in cols if df_out[c].dropna().shape[0] >= 2]
            if valid:
                pt = PowerTransformer(method="yeo-johnson")
                df_out[valid] = pt.fit_transform(df_out[valid])
                journal.append(f"Transformation Yeo-Johnson appliquée : {valid}")

        elif action_id == "transform_skewed":
            cols = [c for c in context["cols_skewed"] if c in df_out.columns]
            for col in cols:
                data = df_out[col].dropna()
                if data.empty:
                    continue
                if data.min() >= 0:
                    df_out[col] = np.log1p(df_out[col])
                    journal.append(f"log1p appliqué → {col}")
                else:
                    shift = abs(data.min()) + 1
                    df_out[col] = np.log1p(df_out[col] + shift)
                    journal.append(f"log1p(x + {shift:.2f}) appliqué → {col}")

        elif action_id == "drop_low_importance":
            cols = context["cols_low_imp"]
            df_out = df_out.drop(columns=[c for c in cols if c in df_out.columns])
            journal.append(f"Variables peu importantes supprimées : {cols}")

        elif action_id == "encode_categorical":
            cat_cols = [c for c in context["cat_cols"] if c in df_out.columns]
            if cat_cols:
                df_out = pd.get_dummies(df_out, columns=cat_cols, drop_first=False)
                journal.append(f"One-Hot Encoding appliqué : {cat_cols}")

        elif action_id == "standardize":
            from sklearn.preprocessing import StandardScaler
            num_cols = df_out.select_dtypes(include="number").columns.tolist()
            if num_cols:
                sc = StandardScaler()
                df_out[num_cols] = sc.fit_transform(df_out[num_cols])
                journal.append(f"Standardisation (Z-score) appliquée : {len(num_cols)} variables")

    return df_out, journal


def apply_recommendations(dataset_id: str, action_ids: List[str]) -> Dict[str, Any]:
    """
    Applique les transformations sélectionnées et crée un NOUVEAU dataset
    (voir note de conception en tête de module). Le dataset d'origine n'est
    jamais modifié.
    """
    record = eda_service.get_dataset(dataset_id)
    df, meta, imp_df = record["df"], record["meta"], record["imp_df"]

    context = _build_context(df, meta, imp_df)
    df_new, journal = _apply_transformations(df, action_ids, context)

    # Reconstruction d'un anon_map pour le nouveau dataset (même logique que app_eda.py)
    orig_inv = {v: k for k, v in meta["mapping"].items()}  # anon -> orig
    new_anon_map = {orig_inv.get(col, col): col for col in df_new.columns}

    filename = f"{record['filename'].rsplit('.', 1)[0]}_transformed.csv"
    derived = eda_service.register_derived_dataset(
        df=df_new,
        anon_map=new_anon_map,
        filename=filename,
        parent_dataset_id=dataset_id,
        transform_journal=journal,
    )

    apercu_df = df_new.head(10).astype(object).where(pd.notnull(df_new.head(10)), None)

    return {
        "dataset_id": dataset_id,
        "new_dataset_id": derived["dataset_id"],
        "journal": journal,
        "comparaison": {
            "lignes_avant": len(df),
            "lignes_apres": len(df_new),
            "colonnes_avant": len(df.columns),
            "colonnes_apres": len(df_new.columns),
            "manquants_avant": int(df.isnull().sum().sum()),
            "manquants_apres": int(df_new.isnull().sum().sum()),
        },
        "apercu": apercu_df.to_dict(orient="records"),
    }
