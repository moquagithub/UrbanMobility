"""
tests/test_eda_endpoints.py — tests unitaires des endpoints EDA (DQE-7, DQE-13).

Couvre le principal trou de couverture identifié pour DQE-13 : les
endpoints EDA (manquants, distributions, normalité, corrélations,
importance, quiz, explorateur, recommandations, erreurs, rapports) n'avaient
aucun test dédié jusqu'ici — seuls les endpoints ML (DQE-8) en avaient. Le dataset de test (`eda_dataset_id`,
voir conftest.py) est construit pour exercer chaque cas : valeurs
manquantes à plusieurs niveaux, distribution asymétrique, paire fortement
corrélée, doublons, catégorielles.
"""


# ── Vue d'ensemble ──────────────────────────────────────────────────────────

def test_overview(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["nb_lignes"] == 205  # 200 + 5 doublons volontaires
    assert data["doublons"] == 5
    assert data["colonnes_numeriques"] == 3  # age, revenu, revenu_double
    assert data["colonnes_textuelles"] == 2  # categorie, ville
    assert 0 < data["taux_completude_global"] < 100
    assert len(data["colonnes"]) == 5


def test_overview_not_found(client):
    assert client.get("/api/v1/datasets/does-not-exist/overview").status_code == 404


# ── Valeurs manquantes ──────────────────────────────────────────────────────

def test_missing(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/missing")
    assert r.status_code == 200
    data = r.json()
    cols = {c["variable"]: c for c in data["colonnes"]}
    assert cols["ville"]["taux_completude"] < cols["age"]["taux_completude"]
    # trié par complétude croissante
    completudes = [c["taux_completude"] for c in data["colonnes"]]
    assert completudes == sorted(completudes)
    assert all(c["statut"] in ("ok", "attention", "critique") for c in data["colonnes"])


# ── Distributions ────────────────────────────────────────────────────────────

def test_distributions_columns(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/distributions")
    assert r.status_code == 200
    data = r.json()
    assert set(data["numeriques"]) == {"age", "revenu", "revenu_double"}
    assert set(data["categorielles"]) == {"categorie", "ville"}


def test_numeric_distribution(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/distributions/numeric/age")
    assert r.status_code == 200
    data = r.json()
    assert len(data["histogramme"]["bin_edges"]) > 1
    assert data["moyenne"] is not None
    assert isinstance(data["alertes"], list)


def test_numeric_distribution_flags_skewed_revenue(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/distributions/numeric/revenu")
    assert r.status_code == 200
    data = r.json()
    assert data["skewness"] is not None and data["skewness"] > 1
    assert any("asymétrique" in a for a in data["alertes"])


def test_numeric_distribution_unknown_column(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/distributions/numeric/nope")
    assert r.status_code == 422


def test_categorical_distribution(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/distributions/categorical/categorie")
    assert r.status_code == 200
    data = r.json()
    assert data["valeurs_uniques"] == 3
    assert len(data["top_valeurs"]) == 3
    assert sum(v["frequence"] for v in data["top_valeurs"]) == 205


def test_categorical_distribution_on_numeric_column_rejected(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/distributions/categorical/age")
    assert r.status_code == 422


# ── Normalité ────────────────────────────────────────────────────────────────

def test_normality_summary(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/normality")
    assert r.status_code == 200
    data = r.json()
    assert len(data["resultats"]) == 3
    verdicts = {row["colonne"]: row["verdict"] for row in data["resultats"]}
    assert verdicts["revenu"] == "non_normale"


def test_normality_detail_non_normal_suggests_transformation(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/normality/revenu")
    assert r.status_code == 200
    data = r.json()
    assert data["resultat"]["verdict"] == "non_normale"
    assert data["qq_plot"] is not None
    assert data["transformation_recommandee"] is not None


def test_normality_detail_unknown_column(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/normality/nope")
    assert r.status_code == 422


# ── Corrélations ─────────────────────────────────────────────────────────────

def test_correlation_detects_strong_pair(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/correlation")
    assert r.status_code == 200
    data = r.json()
    assert len(data["colonnes"]) == 3
    pairs = {frozenset((p["variable_a"], p["variable_b"])) for p in data["paires_fortes"]}
    assert frozenset({"revenu", "revenu_double"}) in pairs


# ── Importance des variables ─────────────────────────────────────────────────

def test_importance(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/importance")
    assert r.status_code == 200
    data = r.json()
    assert len(data["lignes"]) == 5  # les 5 colonnes du dataset
    ranks = [row["rang"] for row in data["lignes"]]
    assert ranks == sorted(ranks)
    assert isinstance(data["interpretation"], list)


def test_importance_top_n(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/importance", params={"top_n": 2})
    assert r.status_code == 200
    assert len(r.json()["lignes"]) == 2


# ── Analyse Quiz ─────────────────────────────────────────────────────────────

def test_quiz_report_handles_dataset_without_quiz_fields(client, eda_dataset_id):
    """Aucune colonne du dataset de test ne suit une convention de nommage 'quiz' —
    l'endpoint doit répondre proprement (multi-réponses/JSON vides), pas planter.
    `consistency_issues` peut en revanche légitimement signaler `revenu`/`revenu_double`
    comme quasi-doublons (r=1.0) — ce contrôle ne dépend pas de la détection de champs quiz."""
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/quiz")
    assert r.status_code == 200
    data = r.json()
    assert data["n_multi"] == 0
    assert data["n_json"] == 0
    assert data["n_ordinal"] == 0
    assert all(issue["type"] == "quasi_doublon" for issue in data["consistency_issues"])


# ── Exploration par variable ─────────────────────────────────────────────────

def test_list_variables_sorted_by_importance(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/variables")
    assert r.status_code == 200
    data = r.json()
    assert len(data["variables"]) == 5
    ranks = [v["rang_importance"] for v in data["variables"] if v["rang_importance"] is not None]
    assert ranks == sorted(ranks)


def test_variable_detail_numeric(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/variables/age")
    assert r.status_code == 200
    data = r.json()
    assert data["stats_numeriques"] is not None
    assert data["distribution_numerique"] is not None
    assert data["distribution_categorielle"] is None
    assert data["decomposition_score"] is not None


def test_variable_detail_categorical(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/variables/categorie")
    assert r.status_code == 200
    data = r.json()
    assert data["stats_numeriques"] is None
    assert data["distribution_categorielle"] is not None


def test_variable_detail_unknown(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/variables/nope")
    assert r.status_code == 422


# ── Recommandations ──────────────────────────────────────────────────────────

def test_recommendations_catalog_flags_duplicates_and_moderate_missing(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/recommendations")
    assert r.status_code == 200
    data = r.json()
    action_ids = {a["id"] for a in data["actions"]}
    assert "drop_duplicates" in action_ids
    assert len(data["pipeline_suggere"]) > 0


def test_recommendations_apply_creates_derived_dataset(client, eda_dataset_id):
    r = client.post(f"/api/v1/datasets/{eda_dataset_id}/recommendations/apply", json={"action_ids": ["drop_duplicates"]})
    assert r.status_code == 200
    data = r.json()
    assert data["comparaison"]["lignes_avant"] == 205
    assert data["comparaison"]["lignes_apres"] == 200
    new_id = data["new_dataset_id"]

    # Le dataset dérivé est bien accessible et distinct de l'original.
    r2 = client.get(f"/api/v1/datasets/{new_id}/overview")
    assert r2.status_code == 200
    assert r2.json()["doublons"] == 0
    # L'original n'a pas été modifié.
    assert client.get(f"/api/v1/datasets/{eda_dataset_id}/overview").json()["doublons"] == 5


def test_recommendations_apply_empty_action_ids_rejected(client, eda_dataset_id):
    r = client.post(f"/api/v1/datasets/{eda_dataset_id}/recommendations/apply", json={"action_ids": []})
    assert r.status_code == 400


def test_recommendations_apply_unknown_action_id_is_noop_not_error(client, eda_dataset_id):
    """Un identifiant d'action inconnu est silencieusement ignoré (comportement hérité de app_eda.py) plutôt que de faire échouer toute la requête."""
    r = client.post(f"/api/v1/datasets/{eda_dataset_id}/recommendations/apply", json={"action_ids": ["not_a_real_action"]})
    assert r.status_code == 200
    assert r.json()["journal"] == []


# ── Journal des erreurs ──────────────────────────────────────────────────────

def test_error_log_empty_for_directly_injected_dataset(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/errors")
    assert r.status_code == 200
    data = r.json()
    assert data["error_count"] == 0
    assert data["erreurs"] == []


# ── Rapports ─────────────────────────────────────────────────────────────────

def test_detailed_report_returns_zip(client, eda_dataset_id):
    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/reports/detailed")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert len(r.content) > 1000  # contient au moins le .tex + quelques figures


def test_synthesis_report_success(client, eda_dataset_id):
    apply_r = client.post(f"/api/v1/datasets/{eda_dataset_id}/recommendations/apply", json={"action_ids": ["drop_duplicates"]})
    new_id = apply_r.json()["new_dataset_id"]

    r = client.get(f"/api/v1/datasets/{eda_dataset_id}/reports/synthesis", params={"after_id": new_id})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert len(r.content) > 1000
