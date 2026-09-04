import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { clusterColor } from "@/components/ml/clusterColors";
import { ClusteringInterpretation } from "@/lib/api";

const QUALITY_VARIANT = { ok: "success", warn: "warning", bad: "destructive" } as const;
const RECO_ICON = { ok: CheckCircle2, warn: AlertTriangle, bad: XCircle };

export function InterpretationPanel({ interpretation }: { interpretation: ClusteringInterpretation }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Interprétation</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert variant={QUALITY_VARIANT[interpretation.quality_level]}>
            <AlertDescription>
              <span className="font-medium">Qualité de la séparation : </span>
              {interpretation.quality_text}
            </AlertDescription>
          </Alert>

          <div className="grid gap-3 sm:grid-cols-2">
            {interpretation.profiles.map((p) => (
              <div key={p.cluster_id} className="rounded-md border border-border p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: clusterColor(p.cluster_id) }} />
                    Cluster {p.cluster_id}
                  </span>
                  <Badge variant="outline">
                    {p.size} ({p.pct.toFixed(1)}%)
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">{p.description}</p>
              </div>
            ))}
          </div>

          {interpretation.recommendations.length > 0 && (
            <div className="space-y-2">
              {interpretation.recommendations.map((r, i) => {
                const Icon = RECO_ICON[r.type];
                return (
                  <Alert key={i} variant={QUALITY_VARIANT[r.type]}>
                    <Icon />
                    <AlertDescription>{r.text}</AlertDescription>
                  </Alert>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
