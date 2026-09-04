import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { clusterColor } from "@/components/ml/clusterColors";
import { SilhouettePlotResult } from "@/lib/api";
import type { Data, Shape } from "plotly.js";

export function SilhouettePlot({ silhouette }: { silhouette: SilhouettePlotResult }) {
  const traces: Data[] = [];
  let offset = 0;
  const gap = Math.max(2, Math.round(silhouette.per_cluster.reduce((n, c) => n + c.values.length, 0) * 0.02));

  for (const cluster of silhouette.per_cluster) {
    const n = cluster.values.length;
    traces.push({
      type: "bar",
      orientation: "h",
      name: `Cluster ${cluster.cluster_id}`,
      x: cluster.values,
      y: Array.from({ length: n }, (_, i) => offset + i),
      marker: { color: clusterColor(cluster.cluster_id) },
      hovertemplate: "silhouette : %{x:.3f}<extra></extra>",
    });
    offset += n + gap;
  }

  const avgLine: Partial<Shape> = {
    type: "line",
    x0: silhouette.average ?? 0,
    x1: silhouette.average ?? 0,
    y0: 0,
    y1: offset,
    line: { color: "hsl(0 72% 46%)", dash: "dash", width: 1.5 },
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Diagramme de silhouette</CardTitle>
        <CardDescription>
          Un score par observation, regroupé par cluster · moyenne globale : {silhouette.average?.toFixed(3) ?? "—"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <PlotlyChart
          height={Math.max(300, offset * 1.5)}
          data={traces}
          layout={{
            barmode: "stack",
            showlegend: false,
            yaxis: { showticklabels: false, title: { text: "Observations (par cluster)" } },
            xaxis: { title: { text: "Coefficient de silhouette" } },
            shapes: [avgLine],
          }}
        />
      </CardContent>
    </Card>
  );
}
