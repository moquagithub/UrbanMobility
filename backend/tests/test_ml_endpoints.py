"""
tests/test_ml_endpoints.py — tests unitaires des endpoints ML (DQE-8).

Couverture :
- Endpoints synchrones : data-quality, algorithms, encoding-plan, dendrogram.
- Endpoints asynchrones : clustering (5 algorithmes), k-selection (elbow + BIC/AIC),
  cycle de vie complet d'un job (pending → completed) via GET /ml/jobs/{job_id}.
- Erreurs : dataset introuvable (404), job introuvable (404), params invalides (422),
  colonne inconnue (422).
- Non-régression : dataset dégénéré (variance nulle) ne doit jamais faire planter
  le serveur (segfault t-SNE corrigé dans ml_service.project_tsne_2d).
"""

import pytest

from .conftest import poll_job


# ── Endpoints synchrones ──────────────────────────────────────────────────

def test_data_quality_ok(client, clustering_dataset_id):
    r = client.get(f"/api/v1/datasets/{clustering_dataset_id}/ml/data-quality")
    assert r.status_code == 200
    data = r.json()
    assert data["score"] == "ok"
    assert data["n"] == 300
    assert set(data["num_cols"]) == {"num_0", "num_1", "num_2", "num_3", "num_4"}
    assert data["cat_cols"] == ["categorie"]


def test_data_quality_dataset_not_found(client):
    r = client.get("/api/v1/datasets/does-not-exist/ml/data-quality")
    assert r.status_code == 404


def test_algorithms_ranked(client, clustering_dataset_id):
    r = client.get(f"/api/v1/datasets/{clustering_dataset_id}/ml/algorithms")
    assert r.status_code == 200
    data = r.json()
    algo_ids = {a["algo_id"] for a in data["algorithmes"]}
    assert algo_ids == {"kmeans", "dbscan", "agglomerative", "gmm", "meanshift"}
    scores = [a["score"] for a in data["algorithmes"]]
    assert scores == sorted(scores, reverse=True)  # trié par pertinence décroissante


def test_algorithms_n_features_param(client, clustering_dataset_id):
    r = client.get(f"/api/v1/datasets/{clustering_dataset_id}/ml/algorithms", params={"n_features": 3})
    assert r.status_code == 200
    assert r.json()["n_features_evalue"] == 3


def test_encoding_plan(client, clustering_dataset_id):
    r = client.get(f"/api/v1/datasets/{clustering_dataset_id}/ml/encoding-plan")
    assert r.status_code == 200
    data = r.json()
    assert data["n_variables"] == 1
    assert data["variables"][0]["variable"] == "categorie"
    assert data["variables"][0]["methode"] in ("one_hot_encoding", "label_encoding", "ordinal_encoding")


def test_dendrogram(client, clustering_dataset_id):
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/dendrogram", json={
        "selected_columns": ["num_0", "num_1", "num_2"], "max_n": 100,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["n_observations_echantillonnees"] == 100
    assert len(data["icoord"]) == len(data["dcoord"])
    assert len(data["icoord"]) == data["n_observations_echantillonnees"] - 1  # n-1 fusions pour n feuilles


def test_dendrogram_unknown_column(client, clustering_dataset_id):
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/dendrogram", json={
        "selected_columns": ["nope"], "max_n": 50,
    })
    assert r.status_code == 422


# ── Clustering asynchrone — un test par algorithme ────────────────────────

CLUSTERING_CASES = [
    {"algo_id": "kmeans", "k": 4},
    {"algo_id": "dbscan", "eps": 1.5, "min_samples": 5},
    {"algo_id": "agglomerative", "k": 4, "linkage": "ward"},
    {"algo_id": "gmm", "k": 4, "cov_type": "full"},
    {"algo_id": "meanshift", "bandwidth": "auto"},
]


def _run_clustering(client, dataset_id, algo_params, compute_tsne=False):
    r = client.post(f"/api/v1/datasets/{dataset_id}/ml/clustering", json={
        "selected_columns": ["num_0", "num_1", "num_2", "num_3", "num_4"],
        "cat_cols": ["categorie"],
        "compute_tsne": compute_tsne,
        "algo_params": algo_params,
    })
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    job_id = body["job_id"]
    return poll_job(client, job_id, timeout=90)


def test_clustering_kmeans_finds_four_clusters(client, clustering_dataset_id):
    job = _run_clustering(client, clustering_dataset_id, {"algo_id": "kmeans", "k": 4})
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["metrics"]["n_clusters"] == 4
    assert res["metrics"]["silhouette"] is not None
    assert len(res["labels"]) == 300
    assert res["pca_2d"]["disponible"] is True
    assert len(res["pca_2d"]["points"]) == 300
    assert res["features_used"]["encoded_columns"] == ["categorie_A", "categorie_B", "categorie_C"]
    assert "interpretation" in res and "quality_text" in res["interpretation"]


def test_clustering_kmeans_with_tsne(client, clustering_dataset_id):
    job = _run_clustering(client, clustering_dataset_id, {"algo_id": "kmeans", "k": 4}, compute_tsne=True)
    assert job["status"] == "completed", job.get("error")
    tsne = job["result"]["tsne_2d"]
    assert tsne["disponible"] is True
    assert len(tsne["points"]) == 300


@pytest.mark.parametrize("algo_params", CLUSTERING_CASES)
def test_clustering_all_algorithms_complete(client, clustering_dataset_id, algo_params):
    job = _run_clustering(client, clustering_dataset_id, algo_params)
    assert job["status"] == "completed", job.get("error")
    res = job["result"]
    assert res["algo_id"] == algo_params["algo_id"]
    assert res["metrics"]["n_total"] == 300
    assert len(res["cluster_sizes"]) == res["metrics"]["n_clusters"] + (1 if res["metrics"]["n_noise"] > 0 else 0)


def test_clustering_invalid_k_rejected(client, clustering_dataset_id):
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/clustering", json={
        "selected_columns": ["num_0", "num_1"],
        "algo_params": {"algo_id": "kmeans", "k": 1},  # k doit être >= 2
    })
    assert r.status_code == 422


def test_clustering_unknown_column_rejected(client, clustering_dataset_id):
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/clustering", json={
        "selected_columns": ["colonne_inexistante"],
        "algo_params": {"algo_id": "kmeans", "k": 3},
    })
    assert r.status_code == 422


def test_clustering_dataset_not_found(client):
    r = client.post("/api/v1/datasets/does-not-exist/ml/clustering", json={
        "selected_columns": ["num_0"],
        "algo_params": {"algo_id": "kmeans", "k": 3},
    })
    assert r.status_code == 404


def test_clustering_missing_discriminator_rejected(client, clustering_dataset_id):
    """algo_params sans algo_id valide doit échouer la validation Pydantic, pas planter le serveur."""
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/clustering", json={
        "selected_columns": ["num_0", "num_1"],
        "algo_params": {"algo_id": "not_a_real_algo", "k": 3},
    })
    assert r.status_code == 422


# ── k-selection asynchrone ─────────────────────────────────────────────────

def test_k_selection_kmeans_elbow(client, clustering_dataset_id):
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/k-selection", json={
        "algo_family": "kmeans", "selected_columns": ["num_0", "num_1", "num_2", "num_3", "num_4"], "k_max": 8,
    })
    assert r.status_code == 202
    job = poll_job(client, r.json()["job_id"], timeout=60)
    assert job["status"] == "completed", job.get("error")
    result = job["result"]
    assert result["algo_family"] == "kmeans"
    assert result["k_optimal"] == 4  # 4 clusters réellement générés dans le fixture
    assert len(result["resultats"]) >= 1


def test_k_selection_gmm_bic(client, clustering_dataset_id):
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/k-selection", json={
        "algo_family": "gmm", "selected_columns": ["num_0", "num_1", "num_2", "num_3", "num_4"], "k_max": 6,
    })
    assert r.status_code == 202
    job = poll_job(client, r.json()["job_id"], timeout=60)
    assert job["status"] == "completed", job.get("error")
    assert job["result"]["algo_family"] == "gmm"
    assert job["result"]["k_optimal"] == 4


def test_k_selection_dataset_not_found(client):
    r = client.post("/api/v1/datasets/does-not-exist/ml/k-selection", json={
        "algo_family": "kmeans", "selected_columns": ["num_0"],
    })
    assert r.status_code == 404


# ── Statut des jobs ─────────────────────────────────────────────────────────

def test_job_status_not_found(client):
    r = client.get("/api/v1/ml/jobs/does-not-exist")
    assert r.status_code == 404


def test_job_status_lifecycle_fields(client, clustering_dataset_id):
    r = client.post(f"/api/v1/datasets/{clustering_dataset_id}/ml/clustering", json={
        "selected_columns": ["num_0", "num_1"],
        "algo_params": {"algo_id": "kmeans", "k": 2},
    })
    job_id = r.json()["job_id"]
    job = poll_job(client, job_id)
    assert job["job_id"] == job_id
    assert job["job_type"] == "clustering"
    assert job["dataset_id"] == clustering_dataset_id
    assert job["created_at"] is not None
    assert job["started_at"] is not None
    assert job["finished_at"] is not None


# ── Non-régression : données dégénérées ne doivent jamais planter le serveur ──

def test_degenerate_data_does_not_crash_tsne(client, degenerate_dataset_id):
    """
    Avant le correctif de déduplication dans ml_service.project_tsne_2d, un
    clustering sur des colonnes à variance nulle (lignes 100% identiques
    après scaling) provoquait un SEGFAULT de sklearn.manifold.TSNE — pas une
    exception Python récupérable. Ce test verrouille le comportement corrigé :
    le job doit se terminer ('completed' ou 'failed'), jamais tuer le process.
    """
    r = client.post(f"/api/v1/datasets/{degenerate_dataset_id}/ml/clustering", json={
        "selected_columns": ["const_0", "const_1", "const_2", "const_3", "const_4", "const_5"],
        "compute_tsne": True,
        "algo_params": {"algo_id": "kmeans", "k": 3},
    })
    assert r.status_code == 202, r.text
    job = poll_job(client, r.json()["job_id"], timeout=60)

    assert job["status"] in ("completed", "failed")
    if job["status"] == "completed":
        assert job["result"]["tsne_2d"]["disponible"] is False

    # Le serveur doit toujours répondre normalement après coup (pas de crash process).
    r = client.get(f"/api/v1/datasets/{degenerate_dataset_id}/ml/data-quality")
    assert r.status_code == 200
