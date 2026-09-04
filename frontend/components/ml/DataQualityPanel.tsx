import { AlertTriangle, XCircle, Info } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { QualityGauge } from "@/components/shared/QualityGauge";
import { MLDataQualityResponse } from "@/lib/api";

export function DataQualityPanel({ quality }: { quality: MLDataQualityResponse }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Qualité des données pour le clustering</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-6">
          <QualityGauge value={quality.q_score} label="score" />
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Observations</div>
              <div className="font-mono tabular-nums">{quality.n.toLocaleString("fr-FR")}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Variables numériques</div>
              <div className="font-mono tabular-nums">{quality.num_cols.length}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Manquants (moy.)</div>
              <div className="font-mono tabular-nums">{quality.miss_pct.toFixed(1)}%</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">Doublons</div>
              <div className="font-mono tabular-nums">
                {quality.dup_n} ({quality.dup_pct.toFixed(1)}%)
              </div>
            </div>
          </div>
        </div>

        {quality.blockers.length > 0 && (
          <div className="space-y-2">
            {quality.blockers.map((b, i) => (
              <Alert key={i} variant="destructive">
                <XCircle />
                <AlertDescription>{b}</AlertDescription>
              </Alert>
            ))}
          </div>
        )}
        {quality.warnings.length > 0 && (
          <div className="space-y-2">
            {quality.warnings.map((w, i) => (
              <Alert key={i} variant="warning">
                <AlertTriangle />
                <AlertDescription>{w}</AlertDescription>
              </Alert>
            ))}
          </div>
        )}
        {quality.infos.length > 0 && (
          <div className="space-y-2">
            {quality.infos.map((info, i) => (
              <Alert key={i}>
                <Info />
                <AlertDescription>{info}</AlertDescription>
              </Alert>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
