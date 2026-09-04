"use client";

import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, ChartSkeleton } from "@/components/shared/States";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getMissing, MissingColumnInfo } from "@/lib/api";

const STATUS_COLOR: Record<MissingColumnInfo["statut"], string> = {
  ok: "hsl(142 66% 30%)",
  attention: "hsl(38 92% 42%)",
  critique: "hsl(0 72% 46%)",
};

const STATUS_BADGE: Record<MissingColumnInfo["statut"], "success" | "warning" | "destructive"> = {
  ok: "success",
  attention: "warning",
  critique: "destructive",
};

export default function MissingPage() {
  const { datasetId } = useDataset();
  const { data, loading, error } = useApiResource(() => getMissing(datasetId), [datasetId]);

  const critiques = data?.colonnes.filter((c) => c.statut === "critique").length ?? 0;
  const attention = data?.colonnes.filter((c) => c.statut === "attention").length ?? 0;

  return (
    <div>
      <PageHeader
        title="Valeurs manquantes"
        description={`Complétude par variable, triée par ordre croissant (seuil critique : < ${data?.seuil_critique_pct ?? 20}% de complétude).`}
      />

      {loading && <ChartSkeleton height={400} />}
      {error && <ErrorState message={error} />}

      {data && (
        <div className="space-y-6">
          <div className="flex gap-2">
            <Badge variant="destructive">{critiques} critique(s)</Badge>
            <Badge variant="warning">{attention} à surveiller</Badge>
            <Badge variant="success">{data.colonnes.length - critiques - attention} correcte(s)</Badge>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Complétude par variable</CardTitle>
              <CardDescription>Les 40 variables les moins complètes</CardDescription>
            </CardHeader>
            <CardContent>
              <PlotlyChart
                height={Math.max(320, data.colonnes.slice(0, 40).length * 20)}
                data={[
                  {
                    type: "bar",
                    orientation: "h",
                    x: data.colonnes.slice(0, 40).map((c) => c.taux_completude),
                    y: data.colonnes.slice(0, 40).map((c) => c.variable),
                    marker: { color: data.colonnes.slice(0, 40).map((c) => STATUS_COLOR[c.statut]) },
                    hovertemplate: "%{y} — %{x:.1f}%% complet<extra></extra>",
                  },
                ]}
                layout={{
                  yaxis: { autorange: "reversed", automargin: true, tickfont: { size: 10 } },
                  xaxis: { title: { text: "Complétude (%)" }, range: [0, 100] },
                }}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Détail par variable</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Variable</TableHead>
                    <TableHead className="text-right">Manquants</TableHead>
                    <TableHead className="text-right">Complétude</TableHead>
                    <TableHead>Statut</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.colonnes.map((col) => (
                    <TableRow key={col.variable}>
                      <TableCell className="font-mono text-xs">{col.variable}</TableCell>
                      <TableCell className="text-right tabular-nums">{col.manquants.toLocaleString("fr-FR")}</TableCell>
                      <TableCell className="text-right tabular-nums">{col.taux_completude.toFixed(1)}%</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_BADGE[col.statut]}>{col.statut}</Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
