import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { clusterColor } from "@/components/ml/clusterColors";
import { ClusterSizeItem } from "@/lib/api";

export function ClusterSizesChart({ sizes }: { sizes: ClusterSizeItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Taille des clusters</CardTitle>
      </CardHeader>
      <CardContent>
        <PlotlyChart
          height={Math.max(200, sizes.length * 40)}
          data={[
            {
              type: "bar",
              orientation: "h",
              x: sizes.map((s) => s.size),
              y: sizes.map((s) => s.cluster_label),
              text: sizes.map((s) => `${s.pct.toFixed(1)}%`),
              textposition: "auto",
              marker: { color: sizes.map((s) => clusterColor(s.cluster_id)) },
            },
          ]}
          layout={{ yaxis: { autorange: "reversed" }, xaxis: { title: { text: "Effectif" } } }}
        />
      </CardContent>
    </Card>
  );
}
