"use client";

import { useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, LoadingState } from "@/components/shared/States";
import { StatCard } from "@/components/shared/StatCard";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { CheckCircle2, ArrowRight, Loader2, Download } from "lucide-react";
import { useDataset } from "@/lib/DatasetContext";
import { useApiResource } from "@/lib/useApiResource";
import { getRecommendations, applyRecommendations, RecommendationsApplyResponse, ApiError, synthesisReportUrl } from "@/lib/api";

const PRIORITY_BADGE = { haute: "destructive", moyenne: "warning", basse: "secondary" } as const;

export default function RecommendationsPage() {
  const { datasetId } = useDataset();
  const { data, loading, error } = useApiResource(() => getRecommendations(datasetId), [datasetId]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [result, setResult] = useState<RecommendationsApplyResponse | null>(null);

  function toggle(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function handleApply() {
    setApplying(true);
    setApplyError(null);
    try {
      const res = await applyRecommendations(datasetId, Array.from(selectedIds));
      setResult(res);
    } catch (err) {
      setApplyError(err instanceof ApiError ? err.message : "Erreur inattendue lors de l'application des transformations.");
    } finally {
      setApplying(false);
    }
  }

  return (
    <div>
      <PageHeader title="Recommandations" description="Actions de préparation de données suggérées automatiquement." />

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="Problèmes critiques" value={data.kpis.problemes_critiques} />
            <StatCard label="Variables à traiter" value={data.kpis.variables_a_traiter} />
            <StatCard label="Paires redondantes" value={data.kpis.paires_redondantes} />
            <StatCard label="Non normales" value={data.kpis.variables_non_normales} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Catalogue d&apos;actions</CardTitle>
              <CardDescription>Sélectionnez les transformations à appliquer — l&apos;original ne sera jamais modifié.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-1">
              {data.actions.length === 0 && <p className="text-sm text-muted-foreground">Aucune action recommandée — les données semblent propres.</p>}
              {data.actions.map((action) => (
                <label
                  key={action.id}
                  className="flex cursor-pointer items-start gap-3 rounded-md border border-transparent p-3 hover:border-border hover:bg-accent/30"
                >
                  <Checkbox checked={selectedIds.has(action.id)} onCheckedChange={() => toggle(action.id)} className="mt-0.5" />
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{action.label}</span>
                      <Badge variant={PRIORITY_BADGE[action.priorite]}>{action.priorite}</Badge>
                      <Badge variant="outline">{action.categorie}</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">{action.justification}</p>
                    <pre className="mt-1 overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-[11px] text-muted-foreground">{action.code}</pre>
                  </div>
                </label>
              ))}
            </CardContent>
          </Card>

          {data.actions.length > 0 && (
            <div className="flex items-center gap-3">
              <Button onClick={handleApply} disabled={selectedIds.size === 0 || applying}>
                {applying && <Loader2 className="h-4 w-4 animate-spin" />}
                Appliquer {selectedIds.size > 0 ? `(${selectedIds.size})` : ""}
              </Button>
              <span className="text-xs text-muted-foreground">Crée un nouveau dataset — celui-ci reste inchangé.</span>
            </div>
          )}

          {applyError && <ErrorState title="Échec de l'application" message={applyError} />}

          {result && (
            <Alert variant="success">
              <CheckCircle2 />
              <AlertTitle>Transformations appliquées</AlertTitle>
              <AlertDescription className="space-y-3">
                <div className="grid grid-cols-3 gap-4 text-xs sm:grid-cols-6">
                  <div>
                    <div className="text-muted-foreground">Lignes</div>
                    <div className="font-mono">{result.comparaison.lignes_avant} → {result.comparaison.lignes_apres}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground">Colonnes</div>
                    <div className="font-mono">{result.comparaison.colonnes_avant} → {result.comparaison.colonnes_apres}</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground">Manquants</div>
                    <div className="font-mono">{result.comparaison.manquants_avant} → {result.comparaison.manquants_apres}</div>
                  </div>
                </div>
                <ul className="list-disc space-y-0.5 pl-4">
                  {result.journal.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button asChild size="sm">
                    <Link href={`/datasets/${result.new_dataset_id}/overview`}>
                      Explorer le dataset transformé <ArrowRight className="h-4 w-4" />
                    </Link>
                  </Button>
                  <Button asChild size="sm" variant="outline">
                    <a href={synthesisReportUrl(datasetId, result.new_dataset_id)} download>
                      <Download className="h-4 w-4" />
                      Rapport de synthèse avant/après (ZIP)
                    </a>
                  </Button>
                </div>
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}
    </div>
  );
}
