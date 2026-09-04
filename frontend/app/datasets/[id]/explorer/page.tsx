"use client";

import { useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, LoadingState, ChartSkeleton } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { cn } from "@/lib/utils";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getVariables, getVariableDetail } from "@/lib/api";

function VariableDetailPanel({ datasetId, column }: { datasetId: string; column: string }) {
  const { data, loading, error } = useApiResource(() => getVariableDetail(datasetId, column), [datasetId, column]);

  if (loading) return <ChartSkeleton height={420} />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="font-mono">{data.nom_anonyme}</CardTitle>
            {data.rang_importance && <Badge variant="outline">Rang importance #{data.rang_importance}</Badge>}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Type</div>
              <div className="font-mono">{data.dtype}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Complétude</div>
              <div className="font-mono tabular-nums">{data.taux_completude.toFixed(1)}%</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Uniques</div>
              <div className="font-mono tabular-nums">{data.valeurs_uniques}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Manquants</div>
              <div className="font-mono tabular-nums">{data.valeurs_manquantes}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {data.decomposition_score && (
        <Card>
          <CardHeader>
            <CardTitle>Décomposition du score d&apos;importance ({data.score_importance?.toFixed(1)})</CardTitle>
          </CardHeader>
          <CardContent>
            <PlotlyChart
              height={200}
              data={[
                {
                  type: "bar",
                  orientation: "h",
                  x: data.decomposition_score.map((d) => d.contribution_pct),
                  y: data.decomposition_score.map((d) => d.critere),
                  marker: { color: "hsl(174 84% 24%)" },
                },
              ]}
              layout={{ yaxis: { automargin: true }, xaxis: { title: { text: "Contribution au score" } } }}
            />
          </CardContent>
        </Card>
      )}

      {data.distribution_numerique && (
        <Card>
          <CardHeader>
            <CardTitle>Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <PlotlyChart
              data={[
                {
                  type: "bar",
                  x: data.distribution_numerique.histogramme.bin_edges.slice(0, -1),
                  y: data.distribution_numerique.histogramme.counts,
                  marker: { color: "hsl(174 84% 24%)" },
                },
              ]}
            />
          </CardContent>
        </Card>
      )}

      {data.distribution_categorielle && (
        <Card>
          <CardHeader>
            <CardTitle>Top valeurs</CardTitle>
          </CardHeader>
          <CardContent>
            <PlotlyChart
              height={Math.max(280, data.distribution_categorielle.top_valeurs.length * 26)}
              data={[
                {
                  type: "bar",
                  orientation: "h",
                  x: data.distribution_categorielle.top_valeurs.map((v) => v.frequence),
                  y: data.distribution_categorielle.top_valeurs.map((v) => v.valeur),
                  marker: { color: "hsl(174 84% 24%)" },
                },
              ]}
              layout={{ yaxis: { autorange: "reversed", automargin: true } }}
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function ExplorerPage() {
  const { datasetId } = useDataset();
  const { data, loading, error } = useApiResource(() => getVariables(datasetId), [datasetId]);
  const [selected, setSelected] = useState<string | undefined>();

  const activeColumn = selected ?? data?.variables[0]?.nom_anonyme;

  return (
    <div>
      <PageHeader title="Exploration par variable" description="Fiche détaillée par variable, triée par importance décroissante." />

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {data && (
        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <Card className="h-fit lg:sticky lg:top-6">
            <CardContent className="max-h-[70vh] overflow-y-auto p-2">
              {data.variables.map((v) => (
                <button
                  key={v.nom_anonyme}
                  onClick={() => setSelected(v.nom_anonyme)}
                  className={cn(
                    "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition-colors",
                    activeColumn === v.nom_anonyme ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                  )}
                >
                  <span className="truncate font-mono text-xs">{v.nom_anonyme}</span>
                  {v.rang_importance && <span className="ml-2 shrink-0 text-xs text-muted-foreground">#{v.rang_importance}</span>}
                </button>
              ))}
            </CardContent>
          </Card>

          {activeColumn && <VariableDetailPanel datasetId={datasetId} column={activeColumn} />}
        </div>
      )}
    </div>
  );
}
