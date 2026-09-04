"use client";

import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, ChartSkeleton } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getCorrelation } from "@/lib/api";

export default function CorrelationPage() {
  const { datasetId } = useDataset();
  const { data, loading, error } = useApiResource(() => getCorrelation(datasetId), [datasetId]);

  return (
    <div>
      <PageHeader title="Corrélations" description="Matrice de corrélation de Pearson entre les variables numériques." />

      {loading && <ChartSkeleton height={500} />}
      {error && <ErrorState message={error} />}

      {data && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Matrice de corrélation</CardTitle>
              <CardDescription>{data.colonnes.length} variables numériques</CardDescription>
            </CardHeader>
            <CardContent>
              <PlotlyChart
                height={Math.max(400, data.colonnes.length * 24)}
                data={[
                  {
                    type: "heatmap",
                    z: data.matrice,
                    x: data.colonnes,
                    y: data.colonnes,
                    colorscale: "RdBu",
                    zmid: 0,
                    zmin: -1,
                    zmax: 1,
                    hovertemplate: "%{x} × %{y} : %{z:.2f}<extra></extra>",
                  } as any,
                ]}
                layout={{
                  xaxis: { tickfont: { size: 9 }, automargin: true },
                  yaxis: { tickfont: { size: 9 }, automargin: true, autorange: "reversed" },
                }}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Paires fortement corrélées</CardTitle>
              <CardDescription>|r| ≥ {data.seuil_forte_correlation}</CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              {data.paires_fortes.length === 0 ? (
                <p className="px-4 pb-4 text-sm text-muted-foreground">Aucune paire fortement corrélée détectée.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Variable A</TableHead>
                      <TableHead>Variable B</TableHead>
                      <TableHead className="text-right">|r|</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.paires_fortes.map((p, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">{p.variable_a}</TableCell>
                        <TableCell className="font-mono text-xs">{p.variable_b}</TableCell>
                        <TableCell className="text-right tabular-nums">{p.r_abs.toFixed(3)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
