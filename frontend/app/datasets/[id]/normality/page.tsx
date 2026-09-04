"use client";

import { useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, ChartSkeleton, LoadingState } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getNormalitySummary, getNormalityDetail } from "@/lib/api";

const VERDICT_BADGE = {
  normale: "success",
  non_normale: "destructive",
  insuffisant: "secondary",
} as const;

function NormalityDetail({ datasetId, column }: { datasetId: string; column: string }) {
  const { data, loading, error } = useApiResource(() => getNormalityDetail(datasetId, column), [datasetId, column]);

  if (loading) return <ChartSkeleton height={380} />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Q-Q plot (droite de Henry)</CardTitle>
        </CardHeader>
        <CardContent>
          {data.qq_plot ? (
            <PlotlyChart
              data={[
                {
                  type: "scatter",
                  mode: "markers",
                  x: data.qq_plot.quantiles_theoriques,
                  y: data.qq_plot.quantiles_observes,
                  marker: { color: "hsl(174 84% 24%)", size: 5 },
                  name: "Observations",
                },
                {
                  type: "scatter",
                  mode: "lines",
                  x: data.qq_plot.droite_x,
                  y: data.qq_plot.droite_y,
                  line: { color: "hsl(38 92% 42%)", dash: "dash" },
                  name: "Normale théorique",
                },
              ]}
              layout={{ xaxis: { title: { text: "Quantiles théoriques" } }, yaxis: { title: { text: "Quantiles observés" } } }}
            />
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">Pas assez de données.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Histogramme vs loi normale</CardTitle>
        </CardHeader>
        <CardContent>
          {data.histogramme ? (
            <PlotlyChart
              data={[
                {
                  type: "bar",
                  x: data.histogramme.bin_edges.slice(0, -1),
                  y: data.histogramme.counts,
                  marker: { color: "hsl(174 45% 80%)" },
                  name: "Observé",
                },
                {
                  type: "scatter",
                  mode: "lines",
                  x: data.histogramme.courbe_x,
                  y: data.histogramme.courbe_y,
                  line: { color: "hsl(0 72% 46%)" },
                  name: "Loi normale",
                },
              ]}
              layout={{ bargap: 0.02 }}
            />
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">Pas assez de données.</p>
          )}
        </CardContent>
      </Card>

      {data.transformation_recommandee && (
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Transformation recommandée</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-foreground">{data.transformation_recommandee.methode}</p>
            <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs text-foreground">
              {data.transformation_recommandee.code_exemple}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function NormalityPage() {
  const { datasetId } = useDataset();
  const { data, loading, error } = useApiResource(() => getNormalitySummary(datasetId), [datasetId]);
  const [selected, setSelected] = useState<string | undefined>();

  const activeColumn = selected ?? data?.resultats[0]?.colonne;

  return (
    <div>
      <PageHeader
        title="Normalité des variables"
        description={`Tests de Shapiro-Wilk, D'Agostino-Pearson et Anderson-Darling (α = ${data?.alpha ?? 0.05}).`}
      />

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {data && (
        <div className="space-y-6">
          <div className="flex gap-2">
            <Badge variant="success">{data.n_normales} normale(s)</Badge>
            <Badge variant="destructive">{data.n_non_normales} non normale(s)</Badge>
            <Badge variant="secondary">{data.n_insuffisantes} insuffisante(s)</Badge>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Résultats par variable</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Variable</TableHead>
                    <TableHead className="text-right">n</TableHead>
                    <TableHead className="text-right">Asymétrie</TableHead>
                    <TableHead className="text-right">Shapiro (p)</TableHead>
                    <TableHead>Verdict</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.resultats.map((r) => (
                    <TableRow
                      key={r.colonne}
                      className="cursor-pointer"
                      onClick={() => setSelected(r.colonne)}
                      data-state={activeColumn === r.colonne ? "selected" : undefined}
                    >
                      <TableCell className="font-mono text-xs">{r.colonne}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.n}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.skewness?.toFixed(3) ?? "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.sw_p?.toFixed(4) ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant={VERDICT_BADGE[r.verdict]}>{r.verdict.replace("_", " ")}</Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-foreground">Détail :</span>
            <Select value={activeColumn} onValueChange={setSelected}>
              <SelectTrigger className="w-64">
                <SelectValue placeholder="Choisir une variable" />
              </SelectTrigger>
              <SelectContent>
                {data.resultats.map((r) => (
                  <SelectItem key={r.colonne} value={r.colonne}>
                    {r.colonne}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {activeColumn && <NormalityDetail datasetId={datasetId} column={activeColumn} />}
        </div>
      )}
    </div>
  );
}
