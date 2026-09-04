"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, PlayCircle, History } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { LoadingState, ErrorState } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { DataQualityPanel } from "@/components/ml/DataQualityPanel";
import { AlgorithmPicker } from "@/components/ml/AlgorithmPicker";
import { ColumnSelector } from "@/components/ml/ColumnSelector";
import { AlgoParamsForm } from "@/components/ml/AlgoParamsForm";
import { KSelectionPanel } from "@/components/ml/KSelectionPanel";
import { DendrogramPanel } from "@/components/ml/DendrogramPanel";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getJobHistory, addJobToHistory, MLJobHistoryEntry } from "@/lib/mlJobHistory";
import {
  getMLDataQuality,
  getMLAlgorithms,
  getEncodingPlan,
  startClustering,
  ApiError,
  AlgoId,
  AlgoParams,
  ScalerType,
  EncodingPlanItem,
} from "@/lib/api";

const DEFAULT_PARAMS: Record<AlgoId, AlgoParams> = {
  kmeans: { algo_id: "kmeans", k: 3, seed: 42 },
  dbscan: { algo_id: "dbscan", eps: 0.5, min_samples: 5 },
  agglomerative: { algo_id: "agglomerative", k: 3, linkage: "ward" },
  gmm: { algo_id: "gmm", k: 3, cov_type: "full" },
  meanshift: { algo_id: "meanshift", bandwidth: "auto" },
};

export default function MLConfigPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const { datasetId } = useDataset();

  const { data: quality, loading: loadingQuality, error: qualityError } = useApiResource(() => getMLDataQuality(datasetId), [datasetId]);
  const { data: algorithms, loading: loadingAlgos, error: algosError } = useApiResource(() => getMLAlgorithms(datasetId), [datasetId]);
  const { data: encodingPlan } = useApiResource(() => getEncodingPlan(datasetId), [datasetId]);

  const [selectedNumCols, setSelectedNumCols] = useState<Set<string>>(new Set());
  const [selectedCatCols, setSelectedCatCols] = useState<Set<string>>(new Set());
  const [scalerType, setScalerType] = useState<ScalerType>("standard");
  const [usePca, setUsePca] = useState(false);
  const [pcaVariance, setPcaVariance] = useState(0.95);
  const [computeTsne, setComputeTsne] = useState(false);
  const [selectedAlgo, setSelectedAlgo] = useState<AlgoId | null>(null);
  const [algoParams, setAlgoParams] = useState<Record<AlgoId, AlgoParams>>(DEFAULT_PARAMS);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [history, setHistory] = useState<MLJobHistoryEntry[]>([]);

  useEffect(() => {
    if (quality) setSelectedNumCols(new Set(quality.num_cols));
  }, [quality]);

  useEffect(() => {
    if (algorithms && !selectedAlgo) setSelectedAlgo(algorithms.algorithmes[0]?.algo_id as AlgoId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [algorithms]);

  useEffect(() => {
    setHistory(getJobHistory(datasetId));
  }, [datasetId]);

  const encodingByVar = useMemo(() => {
    const map: Record<string, EncodingPlanItem> = {};
    encodingPlan?.variables.forEach((v) => (map[v.variable] = v));
    return map;
  }, [encodingPlan]);

  const featureConfig = useMemo(
    () => ({
      selected_columns: Array.from(selectedNumCols),
      cat_cols: Array.from(selectedCatCols),
      scaler_type: scalerType,
      use_pca: usePca,
      pca_variance: pcaVariance,
    }),
    [selectedNumCols, selectedCatCols, scalerType, usePca, pcaVariance]
  );

  function toggleNum(col: string) {
    setSelectedNumCols((prev) => {
      const next = new Set(prev);
      next.has(col) ? next.delete(col) : next.add(col);
      return next;
    });
  }
  function toggleCat(col: string) {
    setSelectedCatCols((prev) => {
      const next = new Set(prev);
      next.has(col) ? next.delete(col) : next.add(col);
      return next;
    });
  }

  const algoFamily: "kmeans" | "agglomerative" | "gmm" | null =
    selectedAlgo === "kmeans" || selectedAlgo === "agglomerative" || selectedAlgo === "gmm" ? selectedAlgo : null;

  async function handleLaunch() {
    if (!selectedAlgo) return;
    setLaunching(true);
    setLaunchError(null);
    try {
      const res = await startClustering(datasetId, {
        ...featureConfig,
        compute_tsne: computeTsne,
        algo_params: algoParams[selectedAlgo],
      });
      addJobToHistory(datasetId, {
        job_id: res.job_id,
        algo_id: selectedAlgo,
        job_type: "clustering",
        launched_at: new Date().toISOString(),
      });
      router.push(`/datasets/${params.id}/ml/${res.job_id}`);
    } catch (err) {
      setLaunchError(err instanceof ApiError ? err.message : "Erreur lors du lancement du clustering.");
      setLaunching(false);
    }
  }

  const canLaunch = Boolean(selectedAlgo) && selectedNumCols.size > 0 && !quality?.blockers.length;

  return (
    <div>
      <PageHeader title="Clustering (ML)" description="Segmentation non supervisée — K-Means, DBSCAN, CAH, GMM, Mean-Shift." />

      {(loadingQuality || loadingAlgos) && <LoadingState />}
      {qualityError && <ErrorState message={qualityError} />}
      {algosError && <ErrorState message={algosError} />}

      {quality && algorithms && (
        <div className="space-y-6">
          <DataQualityPanel quality={quality} />

          <Card>
            <CardHeader>
              <CardTitle>1. Choisir un algorithme</CardTitle>
              <CardDescription>Classés par pertinence estimée pour ce dataset</CardDescription>
            </CardHeader>
            <CardContent>
              <AlgorithmPicker algorithms={algorithms.algorithmes} selected={selectedAlgo} onSelect={setSelectedAlgo} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>2. Variables et prétraitement</CardTitle>
            </CardHeader>
            <CardContent>
              <ColumnSelector
                numCols={quality.num_cols}
                catCols={quality.cat_cols}
                selectedNumCols={selectedNumCols}
                selectedCatCols={selectedCatCols}
                onToggleNum={toggleNum}
                onToggleCat={toggleCat}
                scalerType={scalerType}
                onScalerChange={setScalerType}
                usePca={usePca}
                onUsePcaChange={setUsePca}
                pcaVariance={pcaVariance}
                onPcaVarianceChange={setPcaVariance}
                computeTsne={computeTsne}
                onComputeTsneChange={setComputeTsne}
                encodingPlan={encodingByVar}
              />
            </CardContent>
          </Card>

          {selectedAlgo && (
            <Card>
              <CardHeader>
                <CardTitle>3. Paramètres — {algorithms.algorithmes.find((a) => a.algo_id === selectedAlgo)?.name}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <AlgoParamsForm value={algoParams[selectedAlgo]} onChange={(v) => setAlgoParams((prev) => ({ ...prev, [selectedAlgo]: v }))} />

                <Separator />

                <div className="flex flex-wrap gap-3">
                  {algoFamily && (
                    <KSelectionPanel
                      datasetId={datasetId}
                      algoFamily={algoFamily}
                      featureConfig={featureConfig}
                      onPickK={(k) => setAlgoParams((prev) => ({ ...prev, [selectedAlgo]: { ...prev[selectedAlgo], k } as AlgoParams }))}
                    />
                  )}
                  {selectedAlgo === "agglomerative" && <DendrogramPanel datasetId={datasetId} featureConfig={featureConfig} />}
                </div>
              </CardContent>
            </Card>
          )}

          {launchError && <ErrorState title="Échec du lancement" message={launchError} />}

          <div className="flex items-center gap-3">
            <Button size="lg" onClick={handleLaunch} disabled={!canLaunch || launching}>
              {launching ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
              Lancer le clustering
            </Button>
            {quality.blockers.length > 0 && (
              <span className="text-xs text-destructive">Résolvez les problèmes bloquants ci-dessus avant de lancer une exécution.</span>
            )}
          </div>

          {history.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <History className="h-4 w-4" /> Exécutions récentes
                </CardTitle>
                <CardDescription>Historique de cette session (non persistant)</CardDescription>
              </CardHeader>
              <CardContent className="space-y-1">
                {history.map((h) => (
                  <Link
                    key={h.job_id}
                    href={`/datasets/${params.id}/ml/${h.job_id}`}
                    className="flex items-center justify-between rounded-md px-3 py-2 text-sm hover:bg-accent/40"
                  >
                    <span className="font-mono text-xs">{h.job_id.slice(0, 12)}…</span>
                    <span className="text-muted-foreground">{h.algo_id}</span>
                    <span className="text-xs text-muted-foreground">{new Date(h.launched_at).toLocaleTimeString("fr-FR")}</span>
                  </Link>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
