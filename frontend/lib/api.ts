/**
 * lib/api.ts — Client API pour le backend FastAPI.
 *
 * Un type + une fonction par endpoint, calqués sur les schémas Pydantic du
 * backend (voir backend/app/models/schemas.py). NEXT_PUBLIC_API_URL doit
 * pointer vers le backend (voir .env.local.example).
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      // réponse non-JSON, on garde statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) {
    return undefined as T; // pas de corps
  }
  return res.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return fetch(`${API_URL}${path}`).then((r) => handleResponse<T>(r));
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then((r) => handleResponse<T>(r));
}

/** URL directe (téléchargements — pas de parsing JSON, ouvrir dans un nouvel onglet ou via <a>). */
export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

// ════════════════════════════════════════════════════════════════════════
// Upload & Vue d'ensemble (DQE-6)
// ════════════════════════════════════════════════════════════════════════

export interface UploadResponse {
  dataset_id: string;
  filename: string;
  n_lignes: number;
  n_colonnes: number;
  separateur_detecte: string;
  message: string;
}

export interface ColumnProfile {
  nom_anonyme: string;
  dtype: string;
  valeurs_manquantes: number;
  taux_completude: number;
  valeurs_uniques: number;
  valeur_la_plus_freq: string | null;
}

export interface OverviewResponse {
  dataset_id: string;
  filename: string;
  date_analyse: string;
  nb_lignes: number;
  nb_colonnes: number;
  valeurs_manquantes_total: number;
  taux_completude_global: number;
  colonnes_numeriques: number;
  colonnes_textuelles: number;
  doublons: number;
  colonnes: ColumnProfile[];
  pii_colonnes_traitees: number;
}

export async function uploadCsv(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_URL}/api/v1/datasets/upload`, { method: "POST", body: formData });
  return handleResponse<UploadResponse>(res);
}

export function getOverview(datasetId: string): Promise<OverviewResponse> {
  return get(`/api/v1/datasets/${datasetId}/overview`);
}

// ════════════════════════════════════════════════════════════════════════
// Valeurs manquantes (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface MissingColumnInfo {
  variable: string;
  nom_original: string;
  manquants: number;
  taux_completude: number;
  statut: "ok" | "attention" | "critique";
}

export interface MissingResponse {
  dataset_id: string;
  seuil_critique_pct: number;
  colonnes: MissingColumnInfo[];
}

export function getMissing(datasetId: string): Promise<MissingResponse> {
  return get(`/api/v1/datasets/${datasetId}/missing`);
}

// ════════════════════════════════════════════════════════════════════════
// Distributions (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface DistributionColumnsResponse {
  dataset_id: string;
  numeriques: string[];
  categorielles: string[];
}

export interface HistogramData {
  bin_edges: number[];
  counts: number[];
}

export interface BoxplotData {
  min: number | null;
  q1: number | null;
  median: number | null;
  q3: number | null;
  max: number | null;
  mean: number | null;
  outliers: number[];
}

export interface NumericDistributionResponse {
  dataset_id: string;
  colonne: string;
  nom_original: string;
  histogramme: HistogramData;
  boxplot: BoxplotData;
  moyenne: number | null;
  mediane: number | null;
  ecart_type: number | null;
  skewness: number | null;
  kurtosis: number | null;
  alertes: string[];
}

export interface TopValue {
  valeur: string;
  frequence: number;
}

export interface CategoricalDistributionResponse {
  dataset_id: string;
  colonne: string;
  nom_original: string;
  valeurs_uniques: number;
  mode: string | null;
  top_valeurs: TopValue[];
}

export function getDistributionColumns(datasetId: string): Promise<DistributionColumnsResponse> {
  return get(`/api/v1/datasets/${datasetId}/distributions`);
}
export function getNumericDistribution(datasetId: string, column: string): Promise<NumericDistributionResponse> {
  return get(`/api/v1/datasets/${datasetId}/distributions/numeric/${encodeURIComponent(column)}`);
}
export function getCategoricalDistribution(datasetId: string, column: string): Promise<CategoricalDistributionResponse> {
  return get(`/api/v1/datasets/${datasetId}/distributions/categorical/${encodeURIComponent(column)}`);
}

// ════════════════════════════════════════════════════════════════════════
// Normalité (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface NormalityTestResult {
  colonne: string;
  n: number;
  skewness: number | null;
  kurtosis: number | null;
  sw_stat: number | null;
  sw_p: number | null;
  sw_normal: boolean | null;
  dp_stat: number | null;
  dp_p: number | null;
  dp_normal: boolean | null;
  ad_stat: number | null;
  ad_sig: number | null;
  ad_normal: boolean | null;
  verdict: "normale" | "non_normale" | "insuffisant";
  erreur: string | null;
}

export interface NormalitySummaryResponse {
  dataset_id: string;
  alpha: number;
  n_normales: number;
  n_non_normales: number;
  n_insuffisantes: number;
  resultats: NormalityTestResult[];
}

export interface QQPlotData {
  quantiles_theoriques: (number | null)[];
  quantiles_observes: (number | null)[];
  droite_x: number[];
  droite_y: number[];
}

export interface NormalHistogramData {
  bin_edges: (number | null)[];
  counts: (number | null)[];
  densite: boolean;
  courbe_x: (number | null)[];
  courbe_y: (number | null)[];
}

export interface TransformationSuggestion {
  methode: string;
  code_exemple: string;
}

export interface NormalityDetailResponse {
  dataset_id: string;
  resultat: NormalityTestResult;
  qq_plot: QQPlotData | null;
  histogramme: NormalHistogramData | null;
  transformation_recommandee: TransformationSuggestion | null;
}

export function getNormalitySummary(datasetId: string): Promise<NormalitySummaryResponse> {
  return get(`/api/v1/datasets/${datasetId}/normality`);
}
export function getNormalityDetail(datasetId: string, column: string): Promise<NormalityDetailResponse> {
  return get(`/api/v1/datasets/${datasetId}/normality/${encodeURIComponent(column)}`);
}

// ════════════════════════════════════════════════════════════════════════
// Corrélations (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface CorrelationPair {
  variable_a: string;
  variable_b: string;
  r_abs: number;
}

export interface CorrelationResponse {
  dataset_id: string;
  colonnes: string[];
  matrice: (number | null)[][];
  seuil_forte_correlation: number;
  paires_fortes: CorrelationPair[];
  toutes_paires: CorrelationPair[];
}

export function getCorrelation(datasetId: string): Promise<CorrelationResponse> {
  return get(`/api/v1/datasets/${datasetId}/correlation`);
}

// ════════════════════════════════════════════════════════════════════════
// Importance des variables (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface ImportanceRow {
  nom_anonyme: string;
  nom_original: string;
  rang: number;
  score_importance: number;
  completude_pct: number;
  variabilite_norm: number;
  correlation_max: number;
  unicite_norm: number;
}

export interface ImportanceResponse {
  dataset_id: string;
  top_n: number;
  lignes: ImportanceRow[];
  interpretation: string[];
  methodologie: Record<string, string>;
}

export function getImportance(datasetId: string, topN?: number): Promise<ImportanceResponse> {
  const qs = topN ? `?top_n=${topN}` : "";
  return get(`/api/v1/datasets/${datasetId}/importance${qs}`);
}

// ════════════════════════════════════════════════════════════════════════
// Exploration par variable (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface VariableListItem {
  nom_anonyme: string;
  nom_original: string;
  dtype: string;
  taux_completude: number;
  rang_importance: number | null;
  score_importance: number | null;
}

export interface VariableListResponse {
  dataset_id: string;
  variables: VariableListItem[];
}

export interface ScoreDecomposition {
  critere: string;
  valeur_brute_pct: number;
  contribution_pct: number;
}

export interface VariableDetailResponse {
  dataset_id: string;
  nom_anonyme: string;
  nom_original: string;
  dtype: string;
  taux_completude: number;
  valeurs_uniques: number;
  valeurs_manquantes: number;
  valeur_la_plus_freq: string | null;
  rang_importance: number | null;
  score_importance: number | null;
  stats_numeriques: Record<string, number | null> | null;
  distribution_numerique: NumericDistributionResponse | null;
  distribution_categorielle: CategoricalDistributionResponse | null;
  decomposition_score: ScoreDecomposition[] | null;
}

export function getVariables(datasetId: string): Promise<VariableListResponse> {
  return get(`/api/v1/datasets/${datasetId}/variables`);
}
export function getVariableDetail(datasetId: string, column: string): Promise<VariableDetailResponse> {
  return get(`/api/v1/datasets/${datasetId}/variables/${encodeURIComponent(column)}`);
}

// ════════════════════════════════════════════════════════════════════════
// Recommandations (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface RecommendationAction {
  id: string;
  priorite: "haute" | "moyenne" | "basse";
  categorie: string;
  label: string;
  justification: string;
  code: string;
  impact: string;
  applicable: boolean;
}

export interface RecommendationsKpis {
  problemes_critiques: number;
  variables_a_traiter: number;
  paires_redondantes: number;
  variables_non_normales: number;
}

export interface PipelineStep {
  etape: string;
  action: string;
  outil_suggere: string;
}

export interface RecommendationsResponse {
  dataset_id: string;
  kpis: RecommendationsKpis;
  actions: RecommendationAction[];
  pipeline_suggere: PipelineStep[];
}

export interface ComparisonMetrics {
  lignes_avant: number;
  lignes_apres: number;
  colonnes_avant: number;
  colonnes_apres: number;
  manquants_avant: number;
  manquants_apres: number;
}

export interface RecommendationsApplyResponse {
  dataset_id: string;
  new_dataset_id: string;
  journal: string[];
  comparaison: ComparisonMetrics;
  apercu: Record<string, unknown>[];
}

export function getRecommendations(datasetId: string): Promise<RecommendationsResponse> {
  return get(`/api/v1/datasets/${datasetId}/recommendations`);
}
export function applyRecommendations(datasetId: string, actionIds: string[]): Promise<RecommendationsApplyResponse> {
  return post(`/api/v1/datasets/${datasetId}/recommendations/apply`, { action_ids: actionIds });
}

// ════════════════════════════════════════════════════════════════════════
// Journal des erreurs (DQE-7)
// ════════════════════════════════════════════════════════════════════════

export interface ErrorEntry {
  ligne: number;
  erreur_type: string;
  erreur_msg: string;
  timestamp: string;
  raw_line: string;
  traceback: string;
}

export interface ErrorLogResponse {
  dataset_id: string;
  error_count: number;
  log_path: string | null;
  erreurs: ErrorEntry[];
}

export function getErrorLog(datasetId: string): Promise<ErrorLogResponse> {
  return get(`/api/v1/datasets/${datasetId}/errors`);
}

// ════════════════════════════════════════════════════════════════════════
// Rapports (DQE-7) — téléchargements directs, pas de parsing JSON
// ════════════════════════════════════════════════════════════════════════

export function detailedReportUrl(datasetId: string): string {
  return apiUrl(`/api/v1/datasets/${datasetId}/reports/detailed`);
}
export function synthesisReportUrl(datasetId: string, afterId: string): string {
  return apiUrl(`/api/v1/datasets/${datasetId}/reports/synthesis?after_id=${encodeURIComponent(afterId)}`);
}

// ════════════════════════════════════════════════════════════════════════
// ML — Qualité, catalogue d'algorithmes, plan d'encodage (DQE-8)
// ════════════════════════════════════════════════════════════════════════

export interface MLDataQualityResponse {
  dataset_id: string;
  score: "ok" | "warn" | "bad";
  q_score: number;
  blockers: string[];
  warnings: string[];
  infos: string[];
  n: number;
  p: number;
  num_cols: string[];
  cat_cols: string[];
  zero_var: string[];
  quasi_const: string[];
  miss_pct: number;
  dup_n: number;
  dup_pct: number;
}

export function getMLDataQuality(datasetId: string): Promise<MLDataQualityResponse> {
  return get(`/api/v1/datasets/${datasetId}/ml/data-quality`);
}

export interface AlgorithmInfo {
  algo_id: string;
  score: number;
  name: string;
  icon: string;
  tagline: string;
  complexity: string;
  params: string;
  pros: string[];
  cons: string[];
  best_for: string;
  avoid_if: string;
}

export interface AlgorithmsResponse {
  dataset_id: string;
  n_features_evalue: number;
  algorithmes: AlgorithmInfo[];
}

export function getMLAlgorithms(datasetId: string, nFeatures?: number): Promise<AlgorithmsResponse> {
  const qs = nFeatures ? `?n_features=${nFeatures}` : "";
  return get(`/api/v1/datasets/${datasetId}/ml/algorithms${qs}`);
}

export interface EncodingPlanItem {
  variable: string;
  n_modalites: number;
  pct_manquants: number;
  methode: string;
  raison: string;
  priorite: "haute" | "moyenne" | "basse";
}

export interface EncodingPlanResponse {
  dataset_id: string;
  n_variables: number;
  variables: EncodingPlanItem[];
}

export function getEncodingPlan(datasetId: string): Promise<EncodingPlanResponse> {
  return get(`/api/v1/datasets/${datasetId}/ml/encoding-plan`);
}

// ════════════════════════════════════════════════════════════════════════
// ML — Dendrogramme (synchrone, borné)
// ════════════════════════════════════════════════════════════════════════

export interface DendrogramRequest {
  selected_columns: string[];
  cat_cols?: string[];
  scaler_type?: "standard" | "robust" | "minmax";
  max_n?: number;
}

export interface DendrogramResponse {
  dataset_id: string;
  n_observations_echantillonnees: number;
  n_observations_total: number;
  icoord: number[][];
  dcoord: number[][];
  color_list: string[];
  leaves: number[];
}

export function getDendrogram(datasetId: string, body: DendrogramRequest): Promise<DendrogramResponse> {
  return post(`/api/v1/datasets/${datasetId}/ml/dendrogram`, body);
}

// ════════════════════════════════════════════════════════════════════════
// ML — Configuration commune clustering / k-selection
// ════════════════════════════════════════════════════════════════════════

export type ScalerType = "standard" | "robust" | "minmax";

export interface FeatureConfig {
  selected_columns: string[];
  cat_cols?: string[];
  scaler_type?: ScalerType;
  use_pca?: boolean;
  pca_variance?: number;
}

export type AlgoParams =
  | { algo_id: "kmeans"; k: number; seed?: number }
  | { algo_id: "dbscan"; eps: number; min_samples: number }
  | { algo_id: "agglomerative"; k: number; linkage: "ward" | "complete" | "average" | "single" }
  | { algo_id: "gmm"; k: number; cov_type: "full" | "tied" | "diag" | "spherical" }
  | { algo_id: "meanshift"; bandwidth: "auto" | number };

export type AlgoId = AlgoParams["algo_id"];

// ════════════════════════════════════════════════════════════════════════
// ML — Jobs asynchrones (soumission + suivi de statut)
// ════════════════════════════════════════════════════════════════════════

export interface JobSubmittedResponse {
  job_id: string;
  status: string;
  status_url: string;
}

export interface KSelectionResultRow {
  k: number;
  inertie?: number | null;
  silhouette?: number | null;
  calinski_harabasz?: number | null;
  davies_bouldin?: number | null;
  bic?: number | null;
  aic?: number | null;
}

export interface KSelectionResult {
  algo_family: "kmeans" | "agglomerative" | "gmm";
  resultats: KSelectionResultRow[];
  k_optimal: number | null;
}

export interface ClusteringMetrics {
  n_clusters: number;
  n_noise: number;
  n_total: number;
  silhouette: number | null;
  calinski: number | null;
  davies: number | null;
}

export interface ClusterSizeItem {
  cluster_id: number;
  cluster_label: string;
  size: number;
  pct: number;
}

export interface PCA2DPoint {
  pc1: number | null;
  pc2: number | null;
  cluster_id: number;
  cluster_label: string;
}

export interface PCA2DResult {
  disponible: boolean;
  raison?: string;
  points: PCA2DPoint[];
  explained_variance: (number | null)[];
}

export interface TSNE2DPoint {
  dim1: number | null;
  dim2: number | null;
  cluster_id: number;
  cluster_label: string;
}

export interface TSNE2DResult {
  disponible: boolean;
  raison?: string;
  points: TSNE2DPoint[];
  n_echantillonne: number;
  n_total: number;
}

export interface ClusterProfileSeries {
  cluster_id: number;
  cluster_label: string;
  values: (number | null)[];
}

export interface ClusterProfilesResult {
  disponible: boolean;
  features: string[];
  clusters: ClusterProfileSeries[];
}

export interface ClusterStatItem {
  cluster_id: number;
  cluster_label: string;
  feature: string;
  mean: number | null;
  std: number | null;
  min: number | null;
  q1: number | null;
  median: number | null;
  q3: number | null;
  max: number | null;
}

export interface DiscriminantFeature {
  feature: string;
  f_score: number;
}

export interface SilhouetteClusterSeries {
  cluster_id: number;
  values: (number | null)[];
}

export interface SilhouettePlotResult {
  per_cluster: SilhouetteClusterSeries[];
  average: number | null;
}

export interface ClusteringInterpretationProfile {
  cluster_id: number;
  size: number;
  pct: number;
  description: string;
  top_feature: string | null;
}

export interface ClusteringRecommendation {
  type: "ok" | "warn" | "bad";
  text: string;
}

export interface ClusteringInterpretation {
  quality_text: string;
  quality_level: "ok" | "warn" | "bad";
  profiles: ClusteringInterpretationProfile[];
  recommendations: ClusteringRecommendation[];
}

export interface ClusteringResult {
  dataset_id: string;
  algo_id: AlgoId;
  algo_name: string;
  params_used: Record<string, unknown>;
  features_used: {
    selected_columns: string[];
    cat_cols: string[];
    encoded_columns: string[];
    scaler_type: ScalerType;
    use_pca: boolean;
    pca_variance: number | null;
    explained_variance: (number | null)[] | null;
  };
  metrics: ClusteringMetrics;
  cluster_sizes: ClusterSizeItem[];
  labels: number[];
  pca_2d: PCA2DResult;
  tsne_2d: TSNE2DResult | null;
  cluster_profiles: ClusterProfilesResult;
  cluster_stats: ClusterStatItem[];
  top_discriminant_features: DiscriminantFeature[];
  silhouette_plot: SilhouettePlotResult | null;
  interpretation: ClusteringInterpretation;
}

export interface JobStatusResponse<TResult = ClusteringResult | KSelectionResult> {
  job_id: string;
  job_type: "clustering" | "k_selection";
  dataset_id: string;
  status: "pending" | "running" | "completed" | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result: TResult | null;
  error: string | null;
}

export function startKSelection(
  datasetId: string,
  body: { algo_family: "kmeans" | "agglomerative" | "gmm"; k_max?: number } & FeatureConfig
): Promise<JobSubmittedResponse> {
  return post(`/api/v1/datasets/${datasetId}/ml/k-selection`, body);
}

export function startClustering(
  datasetId: string,
  body: FeatureConfig & { compute_tsne?: boolean; algo_params: AlgoParams }
): Promise<JobSubmittedResponse> {
  return post(`/api/v1/datasets/${datasetId}/ml/clustering`, body);
}

export function getJobStatus<TResult = ClusteringResult | KSelectionResult>(jobId: string): Promise<JobStatusResponse<TResult>> {
  return get(`/api/v1/ml/jobs/${jobId}`);
}

// ════════════════════════════════════════════════════════════════════════
// Datasets enregistrés
// ════════════════════════════════════════════════════════════════════════

export interface DatasetSummary {
  dataset_id: string;
  filename: string | null;
  created_at: string | null;
  source: "upload" | "derived" | null;
  nb_lignes: number | null;
  nb_colonnes: number | null;
}

export interface MyDatasetsResponse {
  datasets: DatasetSummary[];
}

export function getMyDatasets(): Promise<MyDatasetsResponse> {
  return get(`/api/v1/datasets/mine`);
}
