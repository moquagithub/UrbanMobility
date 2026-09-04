"use client";

import { useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorState } from "@/components/shared/States";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { usePollingJob } from "@/lib/usePollingJob";
import { startKSelection, ApiError, FeatureConfig, KSelectionResult } from "@/lib/api";

interface Props {
  datasetId: string;
  algoFamily: "kmeans" | "agglomerative" | "gmm";
  featureConfig: FeatureConfig;
  onPickK: (k: number) => void;
}

export function KSelectionPanel({ datasetId, algoFamily, featureConfig, onPickK }: Props) {
  const [open, setOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const { job, error: pollError } = usePollingJob<KSelectionResult>(jobId);

  async function handleLaunch() {
    setLaunching(true);
    setLaunchError(null);
    try {
      const res = await startKSelection(datasetId, { algo_family: algoFamily, ...featureConfig });
      setJobId(res.job_id);
    } catch (err) {
      setLaunchError(err instanceof ApiError ? err.message : "Erreur lors du lancement de l'analyse.");
    } finally {
      setLaunching(false);
    }
  }

  const result = job?.status === "completed" ? job.result : null;
  const isGmm = algoFamily === "gmm";

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" type="button">
          <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
          Aide au choix de k {isGmm ? "(BIC / AIC)" : "(courbe du coude)"}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-3">
        <Card>
          <CardContent className="space-y-4 p-4">
            <div className="flex items-center gap-3">
              <Button size="sm" onClick={handleLaunch} disabled={launching || job?.status === "running" || job?.status === "pending"}>
                {(launching || job?.status === "running" || job?.status === "pending") && <Loader2 className="h-4 w-4 animate-spin" />}
                Analyser
              </Button>
              {job && (job.status === "pending" || job.status === "running") && (
                <span className="text-xs text-muted-foreground">Calcul en cours…</span>
              )}
              {result?.k_optimal != null && (
                <Button size="sm" variant="secondary" onClick={() => onPickK(result.k_optimal as number)}>
                  Utiliser k = {result.k_optimal}
                </Button>
              )}
            </div>

            {launchError && <ErrorState message={launchError} />}
            {pollError && <ErrorState message={pollError} />}
            {job?.status === "failed" && <ErrorState message={job.error ?? "Échec de l'analyse."} />}

            {result && (
              <PlotlyChart
                height={280}
                data={
                  (isGmm
                    ? [
                        {
                          type: "scatter",
                          mode: "lines+markers",
                          name: "BIC",
                          x: result.resultats.map((r) => r.k),
                          y: result.resultats.map((r) => r.bic ?? null),
                          line: { color: "hsl(174 84% 24%)" },
                        },
                        {
                          type: "scatter",
                          mode: "lines+markers",
                          name: "AIC",
                          x: result.resultats.map((r) => r.k),
                          y: result.resultats.map((r) => r.aic ?? null),
                          line: { color: "hsl(38 92% 42%)" },
                        },
                      ]
                    : [
                        {
                          type: "scatter",
                          mode: "lines+markers",
                          name: "Silhouette",
                          x: result.resultats.map((r) => r.k),
                          y: result.resultats.map((r) => r.silhouette ?? null),
                          line: { color: "hsl(174 84% 24%)" },
                        },
                      ]) as any
                }
                layout={{ xaxis: { title: { text: "k" }, dtick: 1 }, yaxis: { title: { text: isGmm ? "BIC / AIC" : "Silhouette" } } }}
              />
            )}
          </CardContent>
        </Card>
      </CollapsibleContent>
    </Collapsible>
  );
}
