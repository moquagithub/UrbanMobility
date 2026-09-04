"""
tests/conftest.py — fixtures partagées pour les tests des endpoints EDA/ML.

Le dataset de test est injecté DIRECTEMENT dans `dataset_store` plutôt que
via `POST /datasets/upload` : les heuristiques PII de `pii_anonymizer.py`
(legacy, non modifiées) produisent des faux positifs sur des échantillons
de flottants bruts (elles cherchent des patterns proches de numéros de
téléphone) et masqueraient nos colonnes numériques synthétiques — non
pertinent pour tester la couche ML. Le pipeline d'upload/anonymisation est
déjà couvert par ses propres tests.

L'application est en accès libre (aucune authentification) : le fixture
`client` est un simple TestClient, sans session ni cookie.
"""

import sys
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.datasets import make_blobs

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
from app.services import dataset_store  # noqa: E402
from eda_analyse import compute_importance, compute_metadata  # noqa: E402

@pytest.fixture(scope="session")
def client() -> TestClient:
    """Client de test — accès libre, aucune authentification nécessaire."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def clustering_dataset_id() -> str:
    """
    Dataset synthétique à 4 clusters bien séparés (sklearn.make_blobs) +
    1 colonne catégorielle, injecté directement dans dataset_store.
    """
    X, _ = make_blobs(n_samples=300, centers=4, n_features=5, cluster_std=1.2, random_state=42)
    df = pd.DataFrame(X, columns=[f"num_{i}" for i in range(5)])
    df["categorie"] = np.random.RandomState(0).choice(["A", "B", "C"], size=len(df))

    anon_map = {c: c for c in df.columns}
    meta = compute_metadata(df, anon_map)
    imp_df = compute_importance(df)

    dataset_id = uuid.uuid4().hex
    dataset_store.save(dataset_id, {
        "df": df, "anon_map": anon_map, "value_log": {}, "meta": meta, "imp_df": imp_df,
        "filename": "synthetic_clusters.csv",
        "error_snapshot": {"error_count": 0, "log_path": None, "errors": []},
        "created_at": "2026-07-02T00:00:00", "source": "upload",
        "parent_dataset_id": None, "transform_journal": None, "quiz_report": None,
    })
    return dataset_id


@pytest.fixture(scope="module")
def degenerate_dataset_id() -> str:
    """
    Dataset avec des colonnes à variance nulle (toutes valeurs identiques) —
    reproduit le cas qui provoquait un SEGFAULT dans t-SNE avant le correctif
    de déduplication (voir ml_service.project_tsne_2d). Test de non-régression.
    """
    df = pd.DataFrame(np.zeros((250, 6)), columns=[f"const_{i}" for i in range(6)])
    anon_map = {c: c for c in df.columns}
    meta = compute_metadata(df, anon_map)
    imp_df = compute_importance(df)

    dataset_id = uuid.uuid4().hex
    dataset_store.save(dataset_id, {
        "df": df, "anon_map": anon_map, "value_log": {}, "meta": meta, "imp_df": imp_df,
        "filename": "degenerate.csv",
        "error_snapshot": {"error_count": 0, "log_path": None, "errors": []},
        "created_at": "2026-07-02T00:00:00", "source": "upload",
        "parent_dataset_id": None, "transform_journal": None, "quiz_report": None,
    })
    return dataset_id


@pytest.fixture(scope="module")
def eda_dataset_id() -> str:
    """
    Dataset synthétique conçu pour exercer les analyses EDA (DQE-7) :
    - `age` : numérique, quasi complet, proche d'une loi normale.
    - `revenu` : numérique fortement asymétrique (log-normale) — déclenche le
      verdict 'non_normale' et une suggestion de transformation.
    - `revenu_double` : corrélée à `revenu` (r proche de 1) — pour vérifier
      la détection de paires fortement corrélées.
    - `age` a quelques valeurs manquantes (~8%) et `ville` en a davantage
      (~25%, catégorie 'attention' plutôt que 'critique').
    - 5 lignes dupliquées à dessein (`recommendations` doit les détecter).
    - `categorie` / `ville` : catégorielles, pour distributions/encodage.
    """
    rng = np.random.RandomState(42)
    n = 200
    age = rng.normal(40, 8, n).round(1)
    revenu = rng.lognormal(mean=10, sigma=0.6, size=n).round(0)
    df = pd.DataFrame({
        "age": age,
        "revenu": revenu,
        "revenu_double": revenu * 2 + rng.normal(0, 50, n),
        "categorie": rng.choice(["A", "B", "C"], size=n),
        "ville": rng.choice(["Paris", "Lyon", "Marseille", "Nantes"], size=n),
    })
    df.loc[rng.choice(n, size=16, replace=False), "age"] = np.nan
    df.loc[rng.choice(n, size=50, replace=False), "ville"] = np.nan
    df = pd.concat([df, df.iloc[:5]], ignore_index=True)  # 5 doublons volontaires

    anon_map = {c: c for c in df.columns}
    meta = compute_metadata(df, anon_map)
    imp_df = compute_importance(df)

    dataset_id = uuid.uuid4().hex
    dataset_store.save(dataset_id, {
        "df": df, "anon_map": anon_map, "value_log": {}, "meta": meta, "imp_df": imp_df,
        "filename": "eda_synthetic.csv",
        "error_snapshot": {"error_count": 0, "log_path": None, "errors": []},
        "created_at": "2026-07-03T00:00:00", "source": "upload",
        "parent_dataset_id": None, "transform_journal": None, "quiz_report": None,
    })
    return dataset_id


def poll_job(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    """Interroge GET /ml/jobs/{job_id} jusqu'à 'completed'/'failed', ou lève TimeoutError."""
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        data = client.get(f"/api/v1/ml/jobs/{job_id}").json()
        if data["status"] in ("completed", "failed"):
            return data
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} n'a pas terminé en {timeout}s")
