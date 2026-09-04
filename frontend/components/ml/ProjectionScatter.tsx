import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { clusterColor } from "@/components/ml/clusterColors";
import { PCA2DResult, TSNE2DResult } from "@/lib/api";
import type { Data } from "plotly.js";

function groupByCluster<T extends { cluster_id: number; cluster_label: string }>(points: T[]): Map<number, T[]> {
  const map = new Map<number, T[]>();
  for (const p of points) {
    const arr = map.get(p.cluster_id) ?? [];
    arr.push(p);
    map.set(p.cluster_id, arr);
  }
  return map;
}

function pcaTraces(pca: PCA2DResult): Data[] {
  const groups = groupByCluster(pca.points);
  return Array.from(groups.entries()).map(([clusterId, pts]) => ({
    type: "scattergl",
    mode: "markers",
    name: pts[0].cluster_label,
    x: pts.map((p) => p.pc1),
    y: pts.map((p) => p.pc2),
    marker: { color: clusterColor(clusterId), size: 6, opacity: 0.8 },
  }));
}

function tsneTraces(tsne: TSNE2DResult): Data[] {
  const groups = groupByCluster(tsne.points);
  return Array.from(groups.entries()).map(([clusterId, pts]) => ({
    type: "scattergl",
    mode: "markers",
    name: pts[0].cluster_label,
    x: pts.map((p) => p.dim1),
    y: pts.map((p) => p.dim2),
    marker: { color: clusterColor(clusterId), size: 6, opacity: 0.8 },
  }));
}

export function ProjectionScatter({ pca, tsne }: { pca: PCA2DResult; tsne: TSNE2DResult | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Projection 2D</CardTitle>
        <CardDescription>Coloré par cluster — calculé sur les variables standardisées complètes</CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="pca">
          <TabsList>
            <TabsTrigger value="pca">PCA</TabsTrigger>
            <TabsTrigger value="tsne" disabled={!tsne}>
              t-SNE {!tsne && "(non calculé)"}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="pca">
            {pca.disponible ? (
              <PlotlyChart
                data={pcaTraces(pca)}
                layout={{
                  xaxis: { title: { text: `PC1 (${((pca.explained_variance[0] ?? 0) * 100).toFixed(1)}%)` } },
                  yaxis: { title: { text: `PC2 (${((pca.explained_variance[1] ?? 0) * 100).toFixed(1)}%)` } },
                }}
              />
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">{pca.raison ?? "PCA non disponible."}</p>
            )}
          </TabsContent>
          <TabsContent value="tsne">
            {tsne?.disponible ? (
              <>
                <PlotlyChart data={tsneTraces(tsne)} layout={{ xaxis: { title: { text: "Dim 1" } }, yaxis: { title: { text: "Dim 2" } } }} />
                {tsne.n_echantillonne < tsne.n_total && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Sous-échantillonné : {tsne.n_echantillonne} / {tsne.n_total} observations.
                  </p>
                )}
              </>
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">{tsne?.raison ?? "t-SNE non calculé pour cette exécution."}</p>
            )}
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
