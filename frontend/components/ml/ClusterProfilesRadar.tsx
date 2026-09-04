import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { clusterColor } from "@/components/ml/clusterColors";
import { ClusterProfilesResult } from "@/lib/api";
import type { Data } from "plotly.js";

export function ClusterProfilesRadar({ profiles }: { profiles: ClusterProfilesResult }) {
  if (!profiles.disponible) {
    return null;
  }

  const traces: Data[] = profiles.clusters.map((c) => ({
    type: "scatterpolar",
    name: c.cluster_label,
    r: [...c.values, c.values[0]],
    theta: [...profiles.features, profiles.features[0]],
    fill: "toself",
    line: { color: clusterColor(c.cluster_id) },
    opacity: 0.7,
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profils de clusters</CardTitle>
        <CardDescription>Moyenne par cluster, variables normalisées (0-1)</CardDescription>
      </CardHeader>
      <CardContent>
        <PlotlyChart
          height={420}
          data={traces}
          layout={{
            polar: { radialaxis: { visible: true, range: [0, 1] } },
            margin: { l: 60, r: 60, t: 30, b: 30 },
          }}
        />
      </CardContent>
    </Card>
  );
}
