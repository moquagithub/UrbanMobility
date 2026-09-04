"use client";

import Link from "next/link";
import { FileSpreadsheet } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { LoadingState, ErrorState } from "@/components/shared/States";
import { useApiResource } from "@/lib/useApiResource";
import { getMyDatasets } from "@/lib/api";

export function MyDatasetsList() {
  const { data, loading, error } = useApiResource(() => getMyDatasets(), []);

  if (loading) return <LoadingState label="Chargement des datasets…" />;
  if (error) return <ErrorState message={error} />;
  if (!data || data.datasets.length === 0) return null;

  return (
    <Card className="w-full max-w-xl">
      <CardHeader>
        <CardTitle>Datasets</CardTitle>
        <CardDescription>Reprendre une analyse précédente</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {data.datasets.map((d) => (
          <Link
            key={d.dataset_id}
            href={`/datasets/${d.dataset_id}/overview`}
            className="flex items-center justify-between gap-3 rounded-md px-3 py-2 text-sm hover:bg-accent/40"
          >
            <span className="flex items-center gap-2 truncate">
              <FileSpreadsheet className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{d.filename ?? d.dataset_id}</span>
            </span>
            <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              {d.source === "derived" && <Badge variant="outline">transformé</Badge>}
              {d.nb_lignes != null && `${d.nb_lignes.toLocaleString("fr-FR")} lignes`}
            </span>
          </Link>
        ))}
      </CardContent>
    </Card>
  );
}
