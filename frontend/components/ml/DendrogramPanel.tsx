"use client";

import { useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription } from "@/components/ui/card";
import { ErrorState } from "@/components/shared/States";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { getDendrogram, ApiError, FeatureConfig, DendrogramResponse } from "@/lib/api";
import type { Data } from "plotly.js";

interface Props {
  datasetId: string;
  featureConfig: FeatureConfig;
}

const COLOR_MAP: Record<string, string> = {
  C0: "hsl(174 84% 24%)",
  C1: "hsl(38 92% 42%)",
  C2: "hsl(0 72% 46%)",
  C3: "hsl(262 60% 55%)",
};

function dendrogramTraces(d: DendrogramResponse): Data[] {
  return d.icoord.map((xs, i) => ({
    type: "scatter",
    mode: "lines",
    x: xs,
    y: d.dcoord[i],
    line: { color: COLOR_MAP[d.color_list[i]] ?? "hsl(215 16% 55%)", width: 1.5 },
    hoverinfo: "skip",
    showlegend: false,
  }));
}

export function DendrogramPanel({ datasetId, featureConfig }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<DendrogramResponse | null>(null);

  async function handleCompute() {
    setLoading(true);
    setError(null);
    try {
      const res = await getDendrogram(datasetId, { ...featureConfig, max_n: 300 });
      setData(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erreur lors du calcul du dendrogramme.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" type="button">
          <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
          Dendrogramme (classification hiérarchique)
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-3">
        <Card>
          <CardContent className="space-y-4 p-4">
            <div className="flex items-center gap-3">
              <Button size="sm" onClick={handleCompute} disabled={loading}>
                {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                Calculer
              </Button>
              {data && (
                <CardDescription>
                  {data.n_observations_echantillonnees} / {data.n_observations_total} observations (sous-échantillonné)
                </CardDescription>
              )}
            </div>
            {error && <ErrorState message={error} />}
            {data && (
              <PlotlyChart
                height={340}
                data={dendrogramTraces(data)}
                layout={{ xaxis: { showticklabels: false, title: { text: "Observations" } }, yaxis: { title: { text: "Distance" } } }}
              />
            )}
          </CardContent>
        </Card>
      </CollapsibleContent>
    </Collapsible>
  );
}
