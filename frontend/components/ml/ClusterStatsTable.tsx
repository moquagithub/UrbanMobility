import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ClusterStatItem } from "@/lib/api";

function fmt(v: number | null): string {
  return v === null ? "—" : v.toFixed(3);
}

export function ClusterStatsTable({ stats }: { stats: ClusterStatItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Statistiques détaillées par cluster</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="max-h-96 overflow-y-auto">
          <Table>
            <TableHeader className="sticky top-0">
              <TableRow>
                <TableHead>Cluster</TableHead>
                <TableHead>Variable</TableHead>
                <TableHead className="text-right">Moyenne</TableHead>
                <TableHead className="text-right">Écart-type</TableHead>
                <TableHead className="text-right">Min</TableHead>
                <TableHead className="text-right">Médiane</TableHead>
                <TableHead className="text-right">Max</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {stats.map((s, i) => (
                <TableRow key={i}>
                  <TableCell>{s.cluster_label}</TableCell>
                  <TableCell className="font-mono text-xs">{s.feature}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmt(s.mean)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmt(s.std)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmt(s.min)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmt(s.median)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmt(s.max)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
