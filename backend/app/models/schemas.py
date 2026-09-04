"""
models/schemas.py — Schémas Pydantic (requêtes / réponses API).

DQE-7 : ajout des schémas pour les endpoints d'analyse EDA (valeurs
manquantes, distributions, normalité, corrélations, importance, quiz,
explorateur de variable, recommandations, journal des erreurs). Les
schémas de la POC (DQE-6 : Upload/Overview) restent inchangés ci-dessous.
"""

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    """Réponse après upload réussi d'un CSV."""
    dataset_id: str = Field(..., description="Identifiant du dataset pour les appels suivants")
    filename: str
    n_lignes: int
    n_colonnes: int
    separateur_detecte: str
    message: str = "Fichier chargé et anonymisé avec succès."


class ColumnProfile(BaseModel):
    """Profil d'une colonne (issu de compute_metadata)."""
    nom_anonyme: str
    dtype: str
    valeurs_manquantes: int
    taux_completude: float
    valeurs_uniques: int
    valeur_la_plus_freq: Optional[str] = None


class OverviewResponse(BaseModel):
    """Réponse de l'endpoint Vue d'ensemble — équivalent page 'Vue d'ensemble' de app_eda.py."""
    dataset_id:               str
    filename:                 str
    date_analyse:              str
    nb_lignes:                 int
    nb_colonnes:                int
    valeurs_manquantes_total:  int
    taux_completude_global:    float
    colonnes_numeriques:       int
    colonnes_textuelles:       int
    doublons:                  int
    colonnes:                  List[ColumnProfile]
    pii_colonnes_traitees:     int = Field(
        0, description="Nombre de colonnes pour lesquelles une PII a été détectée et anonymisée"
    )


class ErrorResponse(BaseModel):
    detail: str


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Valeurs manquantes (page_missing)
# ════════════════════════════════════════════════════════════════════════

class MissingColumnInfo(BaseModel):
    variable: str
    nom_original: str
    manquants: int
    taux_completude: float
    statut: str = Field(..., description="'ok' | 'attention' | 'critique'")


class MissingResponse(BaseModel):
    dataset_id: str
    seuil_critique_pct: float = 20.0
    colonnes: List[MissingColumnInfo]


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Distributions (page_distributions)
# ════════════════════════════════════════════════════════════════════════

class DistributionColumnsResponse(BaseModel):
    dataset_id: str
    numeriques: List[str]
    categorielles: List[str]


class HistogramData(BaseModel):
    bin_edges: List[float]
    counts: List[int]


class BoxplotData(BaseModel):
    min: Optional[float]
    q1: Optional[float]
    median: Optional[float]
    q3: Optional[float]
    max: Optional[float]
    mean: Optional[float]
    outliers: List[float]


class NumericDistributionResponse(BaseModel):
    dataset_id: str
    colonne: str
    nom_original: str
    histogramme: HistogramData
    boxplot: BoxplotData
    moyenne: Optional[float]
    mediane: Optional[float]
    ecart_type: Optional[float]
    skewness: Optional[float]
    kurtosis: Optional[float]
    alertes: List[str] = Field(default_factory=list)


class TopValue(BaseModel):
    valeur: str
    frequence: int


class CategoricalDistributionResponse(BaseModel):
    dataset_id: str
    colonne: str
    nom_original: str
    valeurs_uniques: int
    mode: Optional[str]
    top_valeurs: List[TopValue]


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Normalité (page_normality)
# ════════════════════════════════════════════════════════════════════════

class NormalityTestResult(BaseModel):
    colonne: str
    n: int
    skewness: Optional[float] = None
    kurtosis: Optional[float] = None
    sw_stat: Optional[float] = None
    sw_p: Optional[float] = None
    sw_normal: Optional[bool] = None
    dp_stat: Optional[float] = None
    dp_p: Optional[float] = None
    dp_normal: Optional[bool] = None
    ad_stat: Optional[float] = None
    ad_sig: Optional[float] = None
    ad_normal: Optional[bool] = None
    verdict: str
    erreur: Optional[str] = None


class NormalitySummaryResponse(BaseModel):
    dataset_id: str
    alpha: float = 0.05
    n_normales: int
    n_non_normales: int
    n_insuffisantes: int
    resultats: List[NormalityTestResult]


class QQPlotData(BaseModel):
    quantiles_theoriques: List[Optional[float]]
    quantiles_observes: List[Optional[float]]
    droite_x: List[float]
    droite_y: List[float]


class NormalHistogramData(BaseModel):
    bin_edges: List[Optional[float]]
    counts: List[Optional[float]]
    densite: bool = True
    courbe_x: List[Optional[float]]
    courbe_y: List[Optional[float]]


class TransformationSuggestion(BaseModel):
    methode: str
    code_exemple: str


class NormalityDetailResponse(BaseModel):
    dataset_id: str
    resultat: NormalityTestResult
    qq_plot: Optional[QQPlotData] = None
    histogramme: Optional[NormalHistogramData] = None
    transformation_recommandee: Optional[TransformationSuggestion] = None


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Corrélations (page_correlation)
# ════════════════════════════════════════════════════════════════════════

class CorrelationPair(BaseModel):
    variable_a: str
    variable_b: str
    r_abs: float


class CorrelationResponse(BaseModel):
    dataset_id: str
    colonnes: List[str]
    matrice: List[List[Optional[float]]] = Field(..., description="Matrice de corrélation de Pearson, ordre = `colonnes` (null si non calculable, ex. colonne entièrement vide)")
    seuil_forte_correlation: float = 0.7
    paires_fortes: List[CorrelationPair]
    toutes_paires: List[CorrelationPair]


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Importance des variables (page_importance)
# ════════════════════════════════════════════════════════════════════════

class ImportanceRow(BaseModel):
    nom_anonyme: str
    nom_original: str
    rang: int
    score_importance: float
    completude_pct: float
    variabilite_norm: float
    correlation_max: float
    unicite_norm: float


class ImportanceResponse(BaseModel):
    dataset_id: str
    top_n: int
    lignes: List[ImportanceRow]
    interpretation: List[str] = Field(
        default_factory=list, description="Paragraphes générés par describe_importance() (eda_analyse.py)"
    )
    methodologie: Dict[str, str] = Field(
        default_factory=lambda: {
            "completude": "30% — proportion de valeurs non manquantes",
            "variabilite": "35% — coefficient de variation (num.) ou entropie de Shannon (cat.)",
            "correlation_max": "20% — corrélation de Pearson max avec les autres variables numériques",
            "unicite": "15% — proportion de valeurs distinctes",
        }
    )


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Analyse Quiz (page_quiz)
# ════════════════════════════════════════════════════════════════════════

class QuizReportResponse(BaseModel):
    dataset_id: str
    n_multi: int
    n_json: int
    n_ordinal: int
    n_freetext: int
    n_filter: int
    n_incoherences: int
    response_rates: List[Dict[str, Any]] = Field(default_factory=list)
    multi_freq: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    multi_coocc: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    json_decoded_preview: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    ordinal_stats: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    consistency_issues: List[Dict[str, Any]] = Field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Exploration par variable (page_variable_explorer)
# ════════════════════════════════════════════════════════════════════════

class VariableListItem(BaseModel):
    nom_anonyme: str
    nom_original: str
    dtype: str
    taux_completude: float
    rang_importance: Optional[int] = None
    score_importance: Optional[float] = None


class VariableListResponse(BaseModel):
    dataset_id: str
    variables: List[VariableListItem]


class ScoreDecomposition(BaseModel):
    critere: str
    valeur_brute_pct: float
    contribution_pct: float


class VariableDetailResponse(BaseModel):
    dataset_id: str
    nom_anonyme: str
    nom_original: str
    dtype: str
    taux_completude: float
    valeurs_uniques: int
    valeurs_manquantes: int
    valeur_la_plus_freq: Optional[str] = None
    rang_importance: Optional[int] = None
    score_importance: Optional[float] = None
    stats_numeriques: Optional[Dict[str, Optional[float]]] = None
    distribution_numerique: Optional[NumericDistributionResponse] = None
    distribution_categorielle: Optional[CategoricalDistributionResponse] = None
    decomposition_score: Optional[List[ScoreDecomposition]] = None


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Recommandations (page_recommendations)
# ════════════════════════════════════════════════════════════════════════

class RecommendationAction(BaseModel):
    id: str
    priorite: str
    categorie: str
    label: str
    justification: str
    code: str
    impact: str
    applicable: bool = True


class RecommendationsKpis(BaseModel):
    problemes_critiques: int
    variables_a_traiter: int
    paires_redondantes: int
    variables_non_normales: int


class PipelineStep(BaseModel):
    etape: str
    action: str
    outil_suggere: str


class RecommendationsResponse(BaseModel):
    dataset_id: str
    kpis: RecommendationsKpis
    actions: List[RecommendationAction]
    pipeline_suggere: List[PipelineStep]


class RecommendationsApplyRequest(BaseModel):
    action_ids: List[str] = Field(..., description="Identifiants des actions du catalogue à appliquer, ex. ['drop_duplicates', 'standardize']")


class ComparisonMetrics(BaseModel):
    lignes_avant: int
    lignes_apres: int
    colonnes_avant: int
    colonnes_apres: int
    manquants_avant: int
    manquants_apres: int


class RecommendationsApplyResponse(BaseModel):
    dataset_id: str = Field(..., description="dataset_id ORIGINAL (non modifié)")
    new_dataset_id: str = Field(..., description="Nouveau dataset créé à partir des transformations — le dataset d'origine reste intact")
    journal: List[str]
    comparaison: ComparisonMetrics
    apercu: List[Dict[str, Any]] = Field(default_factory=list, description="10 premières lignes du dataset transformé")


# ════════════════════════════════════════════════════════════════════════
# DQE-7 — Journal des erreurs (page_errors)
# ════════════════════════════════════════════════════════════════════════

class ErrorEntry(BaseModel):
    ligne: int
    erreur_type: str
    erreur_msg: str
    timestamp: str
    raw_line: str
    traceback: str


class ErrorLogResponse(BaseModel):
    dataset_id: str
    error_count: int
    log_path: Optional[str] = None
    erreurs: List[ErrorEntry] = Field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════
# DQE-8 — Qualité des données ML (assess_data_quality)
# ════════════════════════════════════════════════════════════════════════

class MLDataQualityResponse(BaseModel):
    dataset_id: str
    score: str = Field(..., description="'ok' | 'warn' | 'bad'")
    q_score: int = Field(..., description="Score composite 0-100")
    blockers: List[str]
    warnings: List[str]
    infos: List[str]
    n: int
    p: int
    num_cols: List[str]
    cat_cols: List[str]
    zero_var: List[str]
    quasi_const: List[str]
    miss_pct: float
    dup_n: int
    dup_pct: float


# ════════════════════════════════════════════════════════════════════════
# DQE-8 — Catalogue et recommandation d'algorithmes
# ════════════════════════════════════════════════════════════════════════

class AlgorithmInfo(BaseModel):
    algo_id: str
    score: int
    name: str
    icon: str
    tagline: str
    complexity: str
    params: str
    pros: List[str]
    cons: List[str]
    best_for: str
    avoid_if: str


class AlgorithmsResponse(BaseModel):
    dataset_id: str
    n_features_evalue: int
    algorithmes: List[AlgorithmInfo]


# ════════════════════════════════════════════════════════════════════════
# DQE-8 — Plan d'encodage des variables catégorielles
# ════════════════════════════════════════════════════════════════════════

class EncodingPlanItem(BaseModel):
    variable: str
    n_modalites: int
    pct_manquants: float
    methode: str
    raison: str
    priorite: str


class EncodingPlanResponse(BaseModel):
    dataset_id: str
    n_variables: int
    variables: List[EncodingPlanItem]


# ════════════════════════════════════════════════════════════════════════
# DQE-8 — Requêtes de clustering (validation Pydantic stricte par algo)
# ════════════════════════════════════════════════════════════════════════

class KMeansParams(BaseModel):
    algo_id: Literal["kmeans"] = "kmeans"
    k: int = Field(..., ge=2, description="Nombre de clusters")
    seed: int = 42


class DBSCANParams(BaseModel):
    algo_id: Literal["dbscan"] = "dbscan"
    eps: float = Field(..., gt=0, description="Rayon de voisinage (ε)")
    min_samples: int = Field(..., ge=2)


class AgglomerativeParams(BaseModel):
    algo_id: Literal["agglomerative"] = "agglomerative"
    k: int = Field(..., ge=2)
    linkage: Literal["ward", "complete", "average", "single"] = "ward"


class GMMParams(BaseModel):
    algo_id: Literal["gmm"] = "gmm"
    k: int = Field(..., ge=2)
    cov_type: Literal["full", "tied", "diag", "spherical"] = "full"


class MeanShiftParams(BaseModel):
    algo_id: Literal["meanshift"] = "meanshift"
    bandwidth: Union[Literal["auto"], float] = "auto"


ClusteringAlgoParams = Annotated[
    Union[KMeansParams, DBSCANParams, AgglomerativeParams, GMMParams, MeanShiftParams],
    Field(discriminator="algo_id"),
]


class ClusteringRequest(BaseModel):
    selected_columns: List[str] = Field(..., min_length=1, description="Colonnes numériques utilisées pour le clustering")
    cat_cols: List[str] = Field(default_factory=list, description="Colonnes catégorielles à encoder en one-hot avant clustering")
    scaler_type: Literal["standard", "robust", "minmax"] = "standard"
    use_pca: bool = False
    pca_variance: float = Field(0.95, gt=0, le=1, description="Variance à conserver si use_pca=true")
    compute_tsne: bool = Field(False, description="Calculer aussi la projection t-SNE (lent — désactivé par défaut)")
    algo_params: ClusteringAlgoParams = Field(..., discriminator="algo_id")


class KSelectionRequest(BaseModel):
    algo_family: Literal["kmeans", "agglomerative", "gmm"] = Field(
        ..., description="'kmeans'/'agglomerative' → elbow (inertie/silhouette/Calinski/Davies) ; 'gmm' → BIC/AIC"
    )
    selected_columns: List[str] = Field(..., min_length=1)
    cat_cols: List[str] = Field(default_factory=list)
    scaler_type: Literal["standard", "robust", "minmax"] = "standard"
    use_pca: bool = False
    pca_variance: float = Field(0.95, gt=0, le=1)
    k_max: Optional[int] = Field(None, description="Défaut : 12 (kmeans/agglomerative) ou 10 (gmm)")


class DendrogramRequest(BaseModel):
    selected_columns: List[str] = Field(..., min_length=1)
    cat_cols: List[str] = Field(default_factory=list)
    scaler_type: Literal["standard", "robust", "minmax"] = "standard"
    max_n: int = Field(300, ge=10, le=1000, description="Sous-échantillonnage — coût O(n²log n)")


class DendrogramResponse(BaseModel):
    dataset_id: str
    n_observations_echantillonnees: int
    n_observations_total: int
    icoord: List[List[float]]
    dcoord: List[List[float]]
    color_list: List[str]
    leaves: List[int]


# ════════════════════════════════════════════════════════════════════════
# DQE-8 — Jobs asynchrones (statut + résultat)
# ════════════════════════════════════════════════════════════════════════

class JobSubmittedResponse(BaseModel):
    job_id: str
    status: str = "pending"
    status_url: str = Field(..., description="URL de polling : GET /api/v1/ml/jobs/{job_id}")


class JobStatusResponse(BaseModel):
    job_id: str
    job_type: str = Field(..., description="'clustering' | 'k_selection'")
    dataset_id: str
    status: str = Field(..., description="'pending' | 'running' | 'completed' | 'failed'")
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Présent seulement si status='completed'. Pour job_type='clustering' : "
            "{algo_id, algo_name, params_used, features_used, metrics, cluster_sizes, labels, "
            "pca_2d, tsne_2d, cluster_profiles, cluster_stats, top_discriminant_features, "
            "silhouette_plot, interpretation}. Pour job_type='k_selection' : "
            "{algo_family, resultats, k_optimal}."
        ),
    )
    error: Optional[str] = Field(None, description="Présent seulement si status='failed'")

