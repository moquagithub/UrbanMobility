"use client";

import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, ChartSkeleton } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getImportance } from "@/lib/api";

export default function ImportancePage() {
  const { datasetId } = useDataset();
  const { data, loading, error } = useApiResource(() => getImportance(datasetId, 30), [datasetId]);

  return (
    <div>
      <PageHeader title="Importance des variables" description="Score composite : complétude, variabilité, corrélation max. et unicité." />

      {loading && <ChartSkeleton height={500} />}
      {error && <ErrorState message={error} />}

      {data && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Score d&apos;importance</CardTitle>
              <CardDescription>Top {data.lignes.length} variables</CardDescription>
            </CardHeader>
            <CardContent>
              <PlotlyChart
                height={Math.max(400, data.lignes.length * 24)}
                data={[
                  {
                    type: "bar",
                    orientation: "h",
                    x: [...data.lignes].reverse().map((l) => l.score_importance),
                    y: [...data.lignes].reverse().map((l) => l.nom_anonyme),
                    marker: { color: "hsl(174 84% 24%)" },
                    hovertemplate: "%{y} — score %{x:.1f}<extra></extra>",
                  },
                ]}
                layout={{ xaxis: { title: { text: "Score (0-100)" } }, yaxis: { automargin: true, tickfont: { size: 10 } } }}
              />
            </CardContent>
          </Card>

          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Méthodologie</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs text-muted-foreground">
                {Object.entries(data.methodologie).map(([key, desc]) => (
                  <div key={key}>
                    <span className="font-medium text-foreground">{key}</span> — {desc}
                  </div>
                ))}
              </CardContent>
            </Card>

            {data.interpretation.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Interprétation</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm text-foreground">
                  {data.interpretation.map((p, i) => (
                    <p key={i}>{p}</p>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
