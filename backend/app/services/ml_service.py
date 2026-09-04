"""
services/ml_service.py
========================
Logique métier de `app_ml.py` (module ML de clustering), portée en services
purs — même principe que les services EDA de DQE-7 : la logique de calcul
(scikit-learn, scipy) est conservée à l'identique, seule la couche de
présentation (figures Plotly/matplotlib) est remplacée par des données
brutes JSON, le rendu graphique étant délégué au frontend (DQE-10).

Décision de conception — réutilisation de dataset_store (pas d'upload ML séparé)
----------------------------------------------------------------------------------
Dans les apps Streamlit, `app_ml.py` a son propre `st.file_uploader`,
totalement indépendant de `app_eda.py` : le workflow recommandé était
« nettoyez dans App EDA → exportez le CSV → ré-importez-le dans App ML ».

Cette API est une architecture UNIQUE et décloisonnée (ticket parent DQE-5) :
il n'y a donc pas de sens à dupliquer un second pipeline d'upload/stockage
pour le ML. Les endpoints ML de ce module opèrent directement sur un
`dataset_id` déjà connu de `dataset_store` (issu de `POST /datasets/upload`
ou d'un dataset dérivé via `POST /datasets/{id}/recommendations/apply`,
DQE-7) — le frontend peut proposer "nettoyer dans EDA puis lancer le
clustering sur ces mêmes données" sans ré-upload. C'est un changement
délibéré par rapport au comportement 1:1 de app_ml.py, pas un oubli.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from scipy.cluster.hierarchy import dendrogram, linkage

from sklearn.cluster import DBSCAN, AgglomerativeClustering, KMeans, MeanShift, estimate_bandwidth
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.manifold import TSNE
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_samples, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import MinMaxScaler, RobustScaler, StandardScaler

from app.services import eda_service
from app.services.json_safe import safe_float, safe_float_list

warnings.filterwarnings("ignore")  # même choix que app_ml.py (avertissements sklearn/scipy sur petits échantillons)


# ═══════════════════════════════════════════════════════════
# CATALOGUE DES ALGORITHMES (identique à app_ml.py)
# ═══════════════════════════════════════════════════════════

ALGO_CATALOG: Dict[str, Dict[str, Any]] = {
    "kmeans": {
        "name": "K-Means", "icon": "⚙️",
        "tagline": "Rapide et interprétable — idéal pour démarrer",
        "complexity": "O(n·k·i·d)", "params": "k (nb clusters)",
        "pros": ["Très rapide même sur grands volumes", "Résultats reproductibles", "Facilement interprétable"],
        "cons": ["Suppose des clusters sphériques", "Sensible aux outliers", "k doit être fixé a priori"],
        "best_for": "Données bien séparées, structure globulaire, n > 500",
        "avoid_if": "Clusters de forme irrégulière, bruit important",
    },
    "dbscan": {
        "name": "DBSCAN", "icon": "🌐",
        "tagline": "Détecte les clusters arbitraires et les outliers",
        "complexity": "O(n·log n)", "params": "ε (rayon), min_samples",
        "pros": ["Clusters de forme quelconque", "Détection des outliers automatique", "k automatique"],
        "cons": ["Sensible à ε et min_samples", "Mal adapté aux densités variables", "Difficile en haute dimension"],
        "best_for": "Données avec bruit, clusters non sphériques",
        "avoid_if": "Hautes dimensions sans réduction, densités variables",
    },
    "agglomerative": {
        "name": "Hiérarchique (CAH)", "icon": "🌳",
        "tagline": "Dendrogramme visuel, aucun k fixe requis",
        "complexity": "O(n²·log n)", "params": "k, linkage",
        "pros": ["Dendrogramme pour visualiser la hiérarchie", "k déterminable a posteriori", "Robuste (Ward)"],
        "cons": ["Coûteux pour n > 5 000", "Fusion irréversible", "Lent sur très grands datasets"],
        "best_for": "Petits datasets (n < 5 000), exploration, hiérarchie",
        "avoid_if": "n > 10 000, données en streaming",
    },
    "gmm": {
        "name": "Gaussian Mixture (GMM)", "icon": "🔔",
        "tagline": "Probabilités d'appartenance, clusters elliptiques",
        "complexity": "O(n·k·d²·iter)", "params": "k, covariance_type",
        "pros": ["Probabilités d'appartenance", "Clusters elliptiques", "AIC/BIC pour sélection de k"],
        "cons": ["Suppose des distributions gaussiennes", "Peut diverger si peu de données", "Plus lent que K-Means"],
        "best_for": "Clusters chevauchants, incertitude d'appartenance",
        "avoid_if": "Données très non-gaussiennes, n < 100",
    },
    "meanshift": {
        "name": "Mean Shift", "icon": "🎯",
        "tagline": "k automatique, aucune hypothèse de forme",
        "complexity": "O(n²)", "params": "bandwidth",
        "pros": ["k entièrement automatique", "Aucune hypothèse sur la forme", "Robuste à l'initialisation"],
        "cons": ["Très lent pour n > 3 000", "Sensible à bandwidth", "Pas adapté aux hautes dimensions"],
        "best_for": "Petits datasets (n < 2 000), k totalement inconnu",
        "avoid_if": "n > 5 000, hautes dimensions",
    },
}


# ═══════════════════════════════════════════════════════════
# QUALITÉ DES DONNÉES (assess_data_quality, app_ml.py)
# ═══════════════════════════════════════════════════════════

def assess_data_quality(dataset_id: str) -> Dict[str, Any]:
    df = eda_service.get_dataset(dataset_id)["df"]
    n, p = df.shape
    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(include="object").columns.tolist()
    miss_pct = df.isnull().mean().mean() * 100 if p > 0 else 0.0
    dup_n = int(df.duplicated().sum())
    dup_pct = dup_n / max(n, 1) * 100
    zero_var = [c for c in num_cols if df[c].dropna().std() == 0]
    quasi_const = [
        c for c in num_cols
        if c not in zero_var
        and len(df[c].dropna()) > 0
        and df[c].dropna().value_counts(normalize=True).iloc[0] > 0.95
    ]

    blockers, warnings_list, infos = [], [], []
    if n < 10:
        blockers.append(f"Seulement {n} observations — minimum absolu : 10.")
    elif n < 50:
        warnings_list.append(f"{n} observations — résultats peu fiables. Recommandé : ≥ 100.")

    if not num_cols:
        blockers.append("Aucune variable numérique — le clustering requiert des features numériques.")
    elif len(num_cols) == 1:
        warnings_list.append("1 seule variable numérique — clustering 1D, pertinence limitée.")

    if miss_pct > 50:
        blockers.append(f"{miss_pct:.1f}% de valeurs manquantes — trop élevé pour un clustering fiable.")
    elif miss_pct > 20:
        warnings_list.append(f"{miss_pct:.1f}% de valeurs manquantes — imputation recommandée.")
    elif miss_pct > 0:
        infos.append(f"{miss_pct:.1f}% de valeurs manquantes — seront imputées automatiquement (médiane).")

    if dup_pct > 20:
        warnings_list.append(f"{dup_pct:.1f}% de doublons ({dup_n} lignes).")
    elif dup_pct > 5:
        infos.append(f"{dup_pct:.1f}% de doublons ({dup_n} lignes) — impact limité.")

    if zero_var:
        warnings_list.append(f"{len(zero_var)} variable(s) à variance nulle : {', '.join(zero_var[:4])}")
    if quasi_const:
        infos.append(f"{len(quasi_const)} variable(s) quasi-constante(s) : {', '.join(quasi_const[:4])}")
    if cat_cols:
        infos.append(f"{len(cat_cols)} variable(s) catégorielle(s) détectée(s) — encodables en one-hot (voir /ml/encoding-plan).")

    score = "bad" if blockers else ("warn" if warnings_list else "ok")
    q_score = max(0, min(100, 100 - 40 * len(blockers) - 15 * len(warnings_list)))

    return {
        "dataset_id": dataset_id,
        "score": score, "q_score": q_score,
        "blockers": blockers, "warnings": warnings_list, "infos": infos,
        "n": n, "p": p, "num_cols": num_cols, "cat_cols": cat_cols,
        "zero_var": zero_var, "quasi_const": quasi_const,
        "miss_pct": round(safe_float(miss_pct) or 0.0, 2), "dup_n": dup_n, "dup_pct": round(dup_pct, 2),
    }


# ═══════════════════════════════════════════════════════════
# RECOMMANDATION D'ALGORITHMES (get_recommendations, app_ml.py)
# ═══════════════════════════════════════════════════════════

def _rank_algorithms(n: int, n_features: int, miss_pct: float) -> List[tuple]:
    scores = {
        "kmeans":        min(95, 85 + (5 if n > 500 else 0) - (10 if n_features > 30 else 0)),
        "dbscan":        min(90, 75 + (5 if n < 20000 else -15) - (5 if miss_pct > 10 else 0)),
        "agglomerative": min(85, 70 + (10 if n <= 2000 else -20)),
        "gmm":           min(85, 70 + (5 if n >= 100 else -15) - (20 if n_features > n else 0)),
        "meanshift":     min(75, 60 - (30 if n > 3000 else 0)),
    }
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def get_algorithms(dataset_id: str, n_features: Optional[int] = None) -> Dict[str, Any]:
    """Équivalent du catalogue + du classement de page_quality/page_config (app_ml.py)."""
    qa = assess_data_quality(dataset_id)
    n_feat = n_features if n_features is not None else len(qa["num_cols"])
    ranked = _rank_algorithms(qa["n"], n_feat, qa["miss_pct"])

    algorithms = [
        {"algo_id": algo_id, "score": score, **ALGO_CATALOG[algo_id]}
        for algo_id, score in ranked
    ]
    return {"dataset_id": dataset_id, "n_features_evalue": n_feat, "algorithmes": algorithms}


# ═══════════════════════════════════════════════════════════
# PLAN D'ENCODAGE DES CATÉGORIELLES (recommend_encoding, app_ml.py)
# ═══════════════════════════════════════════════════════════

_ORDINAL_KEYWORDS = {
    "low", "medium", "high", "bas", "moyen", "haut", "faible", "fort",
    "small", "large", "never", "rarely", "sometimes", "often", "always",
    "jamais", "rarement", "souvent", "toujours", "très", "peu",
    "1", "2", "3", "4", "5", "excellent", "bon", "mauvais", "très bon",
}


def _recommend_encoding(col: str, series: pd.Series) -> Dict[str, Any]:
    n_mod = series.nunique()
    miss = series.isnull().mean() * 100
    vals = series.dropna().astype(str).str.lower().tolist()
    is_ordinal = len(set(vals) & _ORDINAL_KEYWORDS) >= 2

    if n_mod == 2:
        method, reason, priority = "label_encoding", "Variable binaire (2 modalités) — label encoding 0/1 suffisant", "haute"
    elif is_ordinal:
        method, reason, priority = "ordinal_encoding", f"Valeurs ordonnées détectées ({n_mod} modalités) — encoder l'ordre", "haute"
    elif n_mod <= 10:
        method, reason, priority = "one_hot_encoding", f"{n_mod} modalités — one-hot sans explosion dimensionnelle", "haute"
    elif n_mod <= 30:
        method, reason, priority = "one_hot_encoding", f"{n_mod} modalités — one-hot acceptable, activer PCA après", "moyenne"
    elif n_mod <= 100:
        method, reason, priority = "frequency_encoding", f"{n_mod} modalités — fréquence d'apparition comme proxy numérique", "moyenne"
    else:
        method, reason, priority = "drop_or_group", f"{n_mod} modalités uniques — probablement un identifiant, à supprimer ou regrouper", "basse"

    return {
        "variable": col, "n_modalites": int(n_mod), "pct_manquants": round(safe_float(miss) or 0.0, 1),
        "methode": method, "raison": reason, "priorite": priority,
    }


def get_encoding_plan(dataset_id: str) -> Dict[str, Any]:
    df = eda_service.get_dataset(dataset_id)["df"]
    cat_cols = df.select_dtypes(include="object").columns.tolist()
    plan = [_recommend_encoding(c, df[c]) for c in cat_cols]
    plan.sort(key=lambda r: {"haute": 0, "moyenne": 1, "basse": 2}[r["priorite"]])
    return {"dataset_id": dataset_id, "n_variables": len(plan), "variables": plan}


# ═══════════════════════════════════════════════════════════
# PRÉPARATION DES FEATURES (prepare_features, app_ml.py)
# ═══════════════════════════════════════════════════════════

def prepare_features(
    df: pd.DataFrame, selected_cols: List[str], scaler_type: str = "standard",
    use_pca: bool = False, pca_variance: float = 0.95, cat_cols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    X_num = df[selected_cols].copy()

    encoded_cols: List[str] = []
    if cat_cols:
        for col in cat_cols:
            if col not in df.columns:
                continue
            dummies = pd.get_dummies(df[col], prefix=col, drop_first=False, dtype=float)
            if dummies.shape[1] > 20:
                top = df[col].value_counts().nlargest(19).index
                dummies = pd.get_dummies(
                    df[col].where(df[col].isin(top), other="__autre__"), prefix=col, drop_first=False, dtype=float
                )
            X_num = pd.concat([X_num, dummies], axis=1)
            encoded_cols.extend(dummies.columns.tolist())

    all_features = selected_cols + encoded_cols

    imp = SimpleImputer(strategy="median")
    X_imp = imp.fit_transform(X_num)

    scalers = {"standard": StandardScaler(), "robust": RobustScaler(), "minmax": MinMaxScaler()}
    scaler = scalers.get(scaler_type, StandardScaler())
    X_scaled = scaler.fit_transform(X_imp)

    pca_model, explained = None, None
    if use_pca and X_scaled.shape[1] > 2:
        pca_model = PCA(n_components=pca_variance, random_state=42)
        X_red = pca_model.fit_transform(X_scaled)
        explained = pca_model.explained_variance_ratio_
    else:
        X_red = X_scaled

    return {
        "X_scaled": X_scaled, "X_reduced": X_red, "pca": pca_model, "explained": explained,
        "features": all_features, "num_features": selected_cols, "encoded_cols": encoded_cols,
    }


# ═══════════════════════════════════════════════════════════
# CLUSTERING (run_clustering, app_ml.py)
# ═══════════════════════════════════════════════════════════

def run_clustering(algo_id: str, X: np.ndarray, params: Dict[str, Any]) -> np.ndarray:
    if algo_id == "kmeans":
        return KMeans(n_clusters=params["k"], random_state=params.get("seed", 42), n_init="auto").fit_predict(X)
    if algo_id == "dbscan":
        return DBSCAN(eps=params["eps"], min_samples=params["min_samples"]).fit_predict(X)
    if algo_id == "agglomerative":
        return AgglomerativeClustering(n_clusters=params["k"], linkage=params["linkage"]).fit_predict(X)
    if algo_id == "gmm":
        return GaussianMixture(n_components=params["k"], covariance_type=params["cov_type"], random_state=42).fit_predict(X)
    if algo_id == "meanshift":
        bw = params["bandwidth"]
        if bw == "auto":
            bw = estimate_bandwidth(X, quantile=0.2, n_samples=min(len(X), 500))
            bw = max(bw, 0.01)
        return MeanShift(bandwidth=float(bw)).fit_predict(X)
    raise ValueError(f"Algorithme inconnu : {algo_id}")


def compute_metrics(X: np.ndarray, labels: np.ndarray) -> Dict[str, Any]:
    noise = int((labels == -1).sum())
    Xv, Lv = X[labels != -1], labels[labels != -1]
    k = len(set(Lv)) if len(Lv) > 0 else 0
    m: Dict[str, Any] = {"n_clusters": k, "n_noise": noise, "n_total": len(labels),
                          "silhouette": None, "calinski": None, "davies": None}
    if k >= 2 and len(Xv) >= k + 1:
        try:
            m["silhouette"] = safe_float(round(float(silhouette_score(Xv, Lv)), 4))
        except Exception:
            pass
        try:
            m["calinski"] = safe_float(round(float(calinski_harabasz_score(Xv, Lv)), 2))
        except Exception:
            pass
        try:
            m["davies"] = safe_float(round(float(davies_bouldin_score(Xv, Lv)), 4))
        except Exception:
            pass
    return m


# ═══════════════════════════════════════════════════════════
# AIDE AU CHOIX DE k (elbow_analysis, gmm_bic_aic — app_ml.py)
# ═══════════════════════════════════════════════════════════

def elbow_analysis(X: np.ndarray, k_max: int = 12) -> List[Dict[str, Any]]:
    rows = []
    k_max = min(k_max, len(X) // 2)
    for k in range(2, k_max + 1):
        km = KMeans(n_clusters=k, random_state=42, n_init="auto")
        lbl = km.fit_predict(X)
        row: Dict[str, Any] = {"k": k, "inertie": safe_float(km.inertia_)}
        if len(set(lbl)) > 1:
            try:
                row["silhouette"] = safe_float(silhouette_score(X, lbl))
            except Exception:
                row["silhouette"] = None
            try:
                row["calinski_harabasz"] = safe_float(calinski_harabasz_score(X, lbl))
            except Exception:
                row["calinski_harabasz"] = None
            try:
                row["davies_bouldin"] = safe_float(davies_bouldin_score(X, lbl))
            except Exception:
                row["davies_bouldin"] = None
        else:
            row["silhouette"] = row["calinski_harabasz"] = row["davies_bouldin"] = None
        rows.append(row)
    return rows


def gmm_bic_aic(X: np.ndarray, k_max: int = 10) -> List[Dict[str, Any]]:
    rows = []
    k_max = min(k_max, max(2, len(X) // 5))
    for k in range(1, k_max + 1):
        gmm = GaussianMixture(n_components=k, random_state=42)
        gmm.fit(X)
        rows.append({"k": k, "bic": safe_float(gmm.bic(X)), "aic": safe_float(gmm.aic(X))})
    return rows


def run_k_selection_job(dataset_id: str, request) -> Dict[str, Any]:
    """Fonction exécutée en tâche de fond par POST /ml/k-selection (elbow ou BIC/AIC selon l'algo)."""
    df = eda_service.get_dataset(dataset_id)["df"]
    prep = prepare_features(
        df, request.selected_columns, request.scaler_type,
        request.use_pca, request.pca_variance, cat_cols=request.cat_cols,
    )
    X = prep["X_reduced"]

    if request.algo_family == "gmm":
        k_max = min(request.k_max or 10, max(2, len(X) // 5))
        rows = gmm_bic_aic(X, k_max=k_max)
        best_k = min(rows, key=lambda r: r["bic"] if r["bic"] is not None else float("inf"))["k"] if rows else None
        return {"algo_family": "gmm", "resultats": rows, "k_optimal": best_k}

    k_max = min(request.k_max or 12, len(X) // 2)
    rows = elbow_analysis(X, k_max=k_max)
    scored = [r for r in rows if r.get("silhouette") is not None]
    best_k = max(scored, key=lambda r: r["silhouette"])["k"] if scored else None
    return {"algo_family": request.algo_family, "resultats": rows, "k_optimal": best_k}


# ═══════════════════════════════════════════════════════════
# DENDROGRAMME (fig_dendro_img, app_ml.py — remplacé par des données brutes)
# ═══════════════════════════════════════════════════════════

def compute_dendrogram(dataset_id: str, selected_columns: List[str], cat_cols: Optional[List[str]],
                        scaler_type: str, max_n: int = 300) -> Dict[str, Any]:
    """
    Équivalent de fig_dendro_img() : au lieu d'un PNG matplotlib, retourne la
    structure du dendrogramme (icoord/dcoord/couleurs) via
    scipy.cluster.hierarchy.dendrogram(..., no_plot=True) — le frontend peut
    la rendre en SVG/Canvas (D3, ou tout autre outil de tracé).
    """
    df = eda_service.get_dataset(dataset_id)["df"]
    prep = prepare_features(df, selected_columns, scaler_type, use_pca=False, cat_cols=cat_cols)
    X_scaled = prep["X_scaled"]

    n = min(len(X_scaled), max_n)
    idx = np.random.RandomState(42).choice(len(X_scaled), n, replace=False)
    Z = linkage(X_scaled[idx], method="ward")
    d = dendrogram(Z, no_plot=True, color_threshold=0.7 * max(Z[:, 2]))

    return {
        "dataset_id": dataset_id,
        "n_observations_echantillonnees": n,
        "n_observations_total": len(X_scaled),
        "icoord": d["icoord"],
        "dcoord": d["dcoord"],
        "color_list": d["color_list"],
        "leaves": [int(x) for x in d["leaves"]],
    }


# ═══════════════════════════════════════════════════════════
# PROJECTIONS 2D (fig_pca2d / fig_tsne2d, app_ml.py)
# ═══════════════════════════════════════════════════════════

def _cluster_label(lbl: int) -> str:
    return "noise" if lbl == -1 else f"cluster_{lbl}"


def project_pca_2d(X_scaled: np.ndarray, labels: np.ndarray) -> Dict[str, Any]:
    if X_scaled.shape[1] < 2:
        return {"disponible": False, "raison": "PCA 2D nécessite ≥ 2 variables.", "points": [], "explained_variance": []}
    pca = PCA(n_components=2, random_state=42)
    coords = pca.fit_transform(X_scaled)
    ev = pca.explained_variance_ratio_
    points = [
        {"pc1": safe_float(coords[i, 0]), "pc2": safe_float(coords[i, 1]),
         "cluster_id": int(labels[i]), "cluster_label": _cluster_label(labels[i])}
        for i in range(len(labels))
    ]
    return {"disponible": True, "points": points, "explained_variance": safe_float_list(ev)}


def project_tsne_2d(X_scaled: np.ndarray, labels: np.ndarray, max_n: int = 3000) -> Dict[str, Any]:
    """
    Équivalent de fig_tsne2d(). Garde-fou important : de nombreuses lignes
    strictement identiques (ex. variables sélectionnées à variance nulle,
    cf. `assess_data_quality.zero_var`) font planter l'implémentation interne
    de TSNE de scikit-learn avec un SEGFAULT (pas une exception Python
    récupérable) — inoffensif pour une session Streamlit mono-utilisateur,
    mais inacceptable pour une API partagée (un seul job malformé tuerait le
    process backend entier). On déduplique donc les lignes avant d'appeler
    TSNE, puis on réassocie chaque observation à son projeté via l'index
    inverse — résultat identique pour l'utilisateur, sans le risque.
    """
    n = min(len(X_scaled), max_n)
    idx = np.random.RandomState(42).choice(len(X_scaled), n, replace=False)
    X_sample = X_scaled[idx]

    X_unique, inverse = np.unique(X_sample, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)  # forme (n,) selon versions de numpy

    if len(X_unique) < 4:
        return {
            "disponible": False,
            "raison": "Trop peu d'observations distinctes pour t-SNE — vérifiez les variables sélectionnées (variance nulle ?).",
            "points": [], "n_echantillonne": n, "n_total": len(X_scaled),
        }

    perp = max(5, min(50, len(X_unique) // 5, len(X_unique) - 1))
    coords_unique = TSNE(n_components=2, perplexity=perp, random_state=42, max_iter=500).fit_transform(X_unique)
    coords = coords_unique[inverse]

    points = [
        {"dim1": safe_float(coords[i, 0]), "dim2": safe_float(coords[i, 1]),
         "cluster_id": int(labels[idx[i]]), "cluster_label": _cluster_label(labels[idx[i]])}
        for i in range(len(idx))
    ]
    return {"disponible": True, "points": points, "n_echantillonne": n, "n_total": len(X_scaled)}


# ═══════════════════════════════════════════════════════════
# PROFILAGE DES CLUSTERS (fig_radar / fig_heatmap / fig_sizes / fig_silhouette_plot)
# ═══════════════════════════════════════════════════════════

def cluster_sizes(labels: np.ndarray) -> List[Dict[str, Any]]:
    n = len(labels)
    vc = pd.Series(labels).value_counts().sort_index()
    return [
        {"cluster_id": int(lbl), "cluster_label": _cluster_label(lbl), "size": int(cnt),
         "pct": round(float(cnt) / n * 100, 1) if n else 0.0}
        for lbl, cnt in vc.items()
    ]


def normalized_cluster_profiles(df: pd.DataFrame, labels: np.ndarray, num_cols: List[str], max_features: int = 12) -> Dict[str, Any]:
    """Équivalent de fig_radar/fig_heatmap : moyenne par cluster des variables normalisées [0,1] (min-max global)."""
    cols = [c for c in num_cols if c in df.columns][:max_features]
    if len(cols) < 2:
        return {"disponible": False, "features": [], "clusters": []}

    df_c = df[cols].copy()
    for c in cols:
        r = df_c[c].max() - df_c[c].min()
        if r and not pd.isna(r) and r > 0:
            df_c[c] = (df_c[c] - df_c[c].min()) / r
        else:
            df_c[c] = 0.0
    df_c["_l"] = labels
    means = df_c[df_c["_l"] >= 0].groupby("_l")[cols].mean()

    clusters = [
        {"cluster_id": int(lbl), "cluster_label": _cluster_label(lbl), "values": safe_float_list(row.tolist())}
        for lbl, row in means.iterrows()
    ]
    return {"disponible": True, "features": cols, "clusters": clusters}


def cluster_stats(df: pd.DataFrame, labels: np.ndarray, num_cols: List[str]) -> List[Dict[str, Any]]:
    """Stats brutes par cluster/variable (mean/std/min/max/q1/q3) — pour tableaux et box/violin côté frontend."""
    df_c = df[num_cols].copy()
    df_c["_l"] = labels
    rows: List[Dict[str, Any]] = []
    for lbl in sorted(set(labels)):
        grp = df_c[df_c["_l"] == lbl]
        for col in num_cols:
            s = grp[col].dropna()
            if s.empty:
                rows.append({"cluster_id": int(lbl), "cluster_label": _cluster_label(lbl), "feature": col,
                             "mean": None, "std": None, "min": None, "q1": None, "median": None, "q3": None, "max": None})
                continue
            q1, med, q3 = s.quantile([0.25, 0.5, 0.75])
            rows.append({
                "cluster_id": int(lbl), "cluster_label": _cluster_label(lbl), "feature": col,
                "mean": safe_float(s.mean()), "std": safe_float(s.std()),
                "min": safe_float(s.min()), "q1": safe_float(q1), "median": safe_float(med),
                "q3": safe_float(q3), "max": safe_float(s.max()),
            })
    return rows


def top_discriminant_features(df: pd.DataFrame, labels: np.ndarray, num_cols: List[str], top_n: int = 10) -> List[Dict[str, Any]]:
    """Équivalent de l'onglet 'Top features discriminantes (ANOVA)' de page_results."""
    df_c = df[num_cols].copy()
    df_c["_l"] = labels
    scores: Dict[str, float] = {}
    for col in num_cols:
        groups = [df_c[df_c["_l"] == lbl][col].dropna() for lbl in set(labels) if lbl >= 0]
        groups = [g for g in groups if len(g) > 1]
        if len(groups) >= 2:
            try:
                f_stat, _ = scipy_stats.f_oneway(*groups)
                scores[col] = float(f_stat) if not np.isnan(f_stat) else 0.0
            except Exception:
                scores[col] = 0.0
        else:
            scores[col] = 0.0
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"feature": col, "f_score": safe_float(score) or 0.0} for col, score in ranked]


def silhouette_plot_data(X: np.ndarray, labels: np.ndarray) -> Optional[Dict[str, Any]]:
    Xv, Lv = X[labels != -1], labels[labels != -1]
    if len(set(Lv)) < 2:
        return None
    try:
        sil_vals = silhouette_samples(Xv, Lv)
    except Exception:
        return None
    per_cluster = [
        {"cluster_id": int(cl), "values": safe_float_list(np.sort(sil_vals[Lv == cl]).tolist())}
        for cl in sorted(set(Lv))
    ]
    return {"per_cluster": per_cluster, "average": safe_float(float(np.mean(sil_vals)))}


# ═══════════════════════════════════════════════════════════
# INTERPRÉTATION AUTOMATIQUE (interpret_results, app_ml.py)
# ═══════════════════════════════════════════════════════════

def interpret_results(df: pd.DataFrame, labels: np.ndarray, num_cols: List[str], metrics: Dict[str, Any], algo_name: str) -> Dict[str, Any]:
    k, noise = metrics["n_clusters"], metrics["n_noise"]
    n = metrics["n_total"]
    sil = metrics["silhouette"]

    if sil is None:
        q_txt, q_lvl = "Non mesurable (< 2 clusters).", "warn"
    elif sil > 0.7:
        q_txt, q_lvl = f"Excellente ({sil:.3f}) — clusters très distincts.", "ok"
    elif sil > 0.5:
        q_txt, q_lvl = f"Bonne ({sil:.3f}) — séparation correcte.", "ok"
    elif sil > 0.25:
        q_txt, q_lvl = f"Modérée ({sil:.3f}) — chevauchement partiel.", "warn"
    else:
        q_txt, q_lvl = f"Faible ({sil:.3f}) — structure peu marquée.", "bad"

    df_c = df[num_cols].copy()
    df_c["_l"] = labels
    df_c = df_c[df_c["_l"] >= 0]
    gm = df[num_cols].mean()
    gs = df[num_cols].std().replace(0, 1)

    profiles = []
    for cl in sorted(df_c["_l"].unique()):
        grp = df_c[df_c["_l"] == cl][num_cols]
        sz = len(grp)
        pct = sz / (n - noise) * 100 if (n - noise) > 0 else 0
        z = ((grp.mean() - gm) / gs).abs()
        top3 = z.nlargest(3)
        parts = []
        for feat, zval in top3.items():
            if zval < 0.3 or pd.isna(zval):
                continue
            direction = "élevé" if grp[feat].mean() > gm[feat] else "faible"
            parts.append(f"{feat} {direction} ({grp[feat].mean():.2f} vs {gm[feat]:.2f})")
        profiles.append({
            "cluster_id": int(cl), "size": int(sz), "pct": round(float(pct), 1),
            "description": ", ".join(parts) if parts else "Profil proche de la moyenne globale",
            "top_feature": top3.index[0] if len(top3) > 0 else None,
        })

    recos = []
    if sil is not None and sil < 0.25:
        recos.append({"type": "warn", "text": "Score de silhouette faible. Essayez : autre k, autre normalisation, autre algorithme (DBSCAN ou GMM)."})
    if n and noise > n * 0.15:
        recos.append({"type": "warn", "text": f"{noise} points ({noise/n*100:.1f}%) classés bruit. Augmentez min_samples ou réduisez ε."})
    if k == 1:
        recos.append({"type": "bad", "text": "1 seul cluster — données homogènes ou paramètres trop restrictifs."})
    if sil is not None and sil > 0.6:
        recos.append({"type": "ok", "text": "Bonne séparation — exploitez ces étiquettes pour segmentation ou analyse ciblée."})

    return {"quality_text": q_txt, "quality_level": q_lvl, "profiles": profiles, "recommendations": recos}


# ═══════════════════════════════════════════════════════════
# ORCHESTRATION DU JOB DE CLUSTERING (tâche de fond)
# ═══════════════════════════════════════════════════════════

def run_clustering_job(dataset_id: str, request) -> Dict[str, Any]:
    """
    Fonction exécutée en tâche de fond par POST /ml/clustering : pipeline complet
    prepare_features → run_clustering → compute_metrics → PCA 2D (toujours) →
    t-SNE 2D (si demandé) → profils de clusters → interprétation automatique.
    """
    df = eda_service.get_dataset(dataset_id)["df"]
    algo_id = request.algo_params.algo_id
    params = request.algo_params.model_dump(exclude={"algo_id"})

    prep = prepare_features(
        df, request.selected_columns, request.scaler_type,
        request.use_pca, request.pca_variance, cat_cols=request.cat_cols,
    )
    labels = run_clustering(algo_id, prep["X_reduced"], params)
    metrics = compute_metrics(prep["X_reduced"], labels)
    num_cols = request.selected_columns

    # Projections 2D — TOUJOURS sur X_scaled (pas X_reduced), comme fig_pca2d/fig_tsne2d dans app_ml.py :
    # la visualisation reste en pleine dimension même si le clustering, lui, a été fait après réduction PCA.
    pca_2d = project_pca_2d(prep["X_scaled"], labels)
    tsne_2d = project_tsne_2d(prep["X_scaled"], labels) if request.compute_tsne else None

    result: Dict[str, Any] = {
        "dataset_id": dataset_id,
        "algo_id": algo_id,
        "algo_name": ALGO_CATALOG[algo_id]["name"],
        "params_used": params,
        "features_used": {
            "selected_columns": request.selected_columns,
            "cat_cols": request.cat_cols or [],
            "encoded_columns": prep["encoded_cols"],
            "scaler_type": request.scaler_type,
            "use_pca": request.use_pca,
            "pca_variance": request.pca_variance if request.use_pca else None,
            "explained_variance": safe_float_list(prep["explained"]) if prep["explained"] is not None else None,
        },
        "metrics": metrics,
        "cluster_sizes": cluster_sizes(labels),
        "labels": [int(lbl) for lbl in labels],
        "pca_2d": pca_2d,
        "tsne_2d": tsne_2d,
        "cluster_profiles": normalized_cluster_profiles(df, labels, num_cols),
        "cluster_stats": cluster_stats(df, labels, num_cols),
        "top_discriminant_features": top_discriminant_features(df, labels, num_cols),
        "silhouette_plot": silhouette_plot_data(prep["X_reduced"], labels),
        "interpretation": interpret_results(df, labels, num_cols, metrics, ALGO_CATALOG[algo_id]["name"]),
    }
    return result
