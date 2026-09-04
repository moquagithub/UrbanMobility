"use client";

import { Download, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDataset } from "@/lib/DatasetContext";
import { detailedReportUrl } from "@/lib/api";

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}

/** En-tête commun à toutes les pages EDA : titre de page + fil d'ariane dataset + actions. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  const { datasetId, overview } = useDataset();

  return (
    <div className="mb-6 flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {overview && (
            <>
              <FileText className="h-3.5 w-3.5" />
              <span className="truncate">{overview.filename}</span>
              <span className="text-border">·</span>
              <span className="font-mono">{datasetId.slice(0, 10)}…</span>
            </>
          )}
        </div>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {actions}
        <Button variant="outline" size="sm" asChild>
          <a href={detailedReportUrl(datasetId)} download>
            <Download className="h-4 w-4" />
            Rapport détaillé (ZIP)
          </a>
        </Button>
      </div>
    </div>
  );
}
