"use client";

import { useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, ChartSkeleton, LoadingState } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getDistributionColumns, getNumericDistribution, getCategoricalDistribution } from "@/lib/api";

function NumericDistribution({ datasetId, column }: { datasetId: string; column: string }) {
  const { data, loading, error } = useApiResource(() => getNumericDistribution(datasetId, column), [datasetId, column]);

  if (loading) return <ChartSkeleton height={640} />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Histogramme</CardTitle>
        </CardHeader>
        <CardContent>
          <PlotlyChart
            data={[
              {
                type: "bar",
                x: data.histogramme.bin_edges.slice(0, -1),
                y: data.histogramme.counts,
                marker: { color: "hsl(174 84% 24%)" },
              },
            ]}
            layout={{ xaxis: { title: { text: data.colonne } }, yaxis: { title: { text: "Effectif" } }, bargap: 0.02 }}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Boîte à moustaches</CardTitle>
        </CardHeader>
        <CardContent>
          {data.boxplot.min !== null ? (
            <PlotlyChart
              data={[
                {
                  type: "box",
                  name: data.colonne,
                  q1: [data.boxplot.q1],
                  median: [data.boxplot.median],
                  q3: [data.boxplot.q3],
                  lowerfence: [data.boxplot.min],
                  upperfence: [data.boxplot.max],
                  mean: [data.boxplot.mean],
                  boxmean: true,
                  y: [data.boxplot.outliers],
                  marker: { color: "hsl(174 84% 24%)" },
                } as any,
              ]}
              layout={{ showlegend: false }}
            />
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">Aucune donnée disponible pour cette variable.</p>
          )}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Statistiques descriptives</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-5">
            {[
              ["Moyenne", data.moyenne],
              ["Médiane", data.mediane],
              ["Écart-type", data.ecart_type],
              ["Asymétrie", data.skewness],
              ["Aplatissement", data.kurtosis],
            ].map(([label, value]) => (
              <div key={label as string}>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
                <div className="mt-0.5 font-mono tabular-nums text-foreground">
                  {value === null || value === undefined ? "—" : (value as number).toFixed(3)}
                </div>
              </div>
            ))}
          </div>
          {data.alertes.length > 0 && (
            <div className="mt-4 space-y-2">
              {data.alertes.map((a, i) => (
                <Alert key={i} variant="warning">
                  <AlertDescription>{a}</AlertDescription>
                </Alert>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function CategoricalDistribution({ datasetId, column }: { datasetId: string; column: string }) {
  const { data, loading, error } = useApiResource(() => getCategoricalDistribution(datasetId, column), [datasetId, column]);

  if (loading) return <ChartSkeleton height={400} />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Top valeurs</CardTitle>
        </CardHeader>
        <CardContent>
          <PlotlyChart
            height={Math.max(300, data.top_valeurs.length * 28)}
            data={[
              {
                type: "bar",
                orientation: "h",
                x: data.top_valeurs.map((v) => v.frequence),
                y: data.top_valeurs.map((v) => v.valeur),
                marker: { color: "hsl(174 84% 24%)" },
              },
            ]}
            layout={{ yaxis: { autorange: "reversed", automargin: true }, xaxis: { title: { text: "Effectif" } } }}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Résumé</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Valeurs uniques</div>
            <div className="font-mono text-lg tabular-nums">{data.valeurs_uniques}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Mode</div>
            <div className="truncate font-medium">{data.mode ?? "—"}</div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default function DistributionsPage() {
  const { datasetId } = useDataset();
  const { data: cols, loading, error } = useApiResource(() => getDistributionColumns(datasetId), [datasetId]);
  const [numCol, setNumCol] = useState<string | undefined>();
  const [catCol, setCatCol] = useState<string | undefined>();

  const activeNumCol = numCol ?? cols?.numeriques[0];
  const activeCatCol = catCol ?? cols?.categorielles[0];

  return (
    <div>
      <PageHeader title="Distributions" description="Forme des variables numériques et catégorielles." />

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {cols && (
        <Tabs defaultValue={cols.numeriques.length ? "numeric" : "categorical"}>
          <TabsList>
            <TabsTrigger value="numeric" disabled={!cols.numeriques.length}>
              Numériques ({cols.numeriques.length})
            </TabsTrigger>
            <TabsTrigger value="categorical" disabled={!cols.categorielles.length}>
              Catégorielles ({cols.categorielles.length})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="numeric" className="space-y-4">
            {cols.numeriques.length > 0 ? (
              <>
                <Select value={activeNumCol} onValueChange={setNumCol}>
                  <SelectTrigger className="w-64">
                    <SelectValue placeholder="Choisir une variable" />
                  </SelectTrigger>
                  <SelectContent>
                    {cols.numeriques.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {activeNumCol && <NumericDistribution datasetId={datasetId} column={activeNumCol} />}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Aucune variable numérique dans ce dataset.</p>
            )}
          </TabsContent>

          <TabsContent value="categorical" className="space-y-4">
            {cols.categorielles.length > 0 ? (
              <>
                <Select value={activeCatCol} onValueChange={setCatCol}>
                  <SelectTrigger className="w-64">
                    <SelectValue placeholder="Choisir une variable" />
                  </SelectTrigger>
                  <SelectContent>
                    {cols.categorielles.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {activeCatCol && <CategoricalDistribution datasetId={datasetId} column={activeCatCol} />}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Aucune variable catégorielle dans ce dataset.</p>
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
