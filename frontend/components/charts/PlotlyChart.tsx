"use client";

import dynamic from "next/dynamic";
import type { Layout, Config, Data } from "plotly.js";
import { cn } from "@/lib/utils";

// Plotly référence `window`/`document` à l'import — incompatible avec le rendu
// serveur de Next.js. Chargement dynamique côté client uniquement (ssr:false).
const Plot = dynamic(() => import("react-plotly.js"), {
  ssr: false,
  loading: () => <div className="flex h-full min-h-[300px] items-center justify-center text-sm text-muted-foreground">Chargement du graphique…</div>,
});

/** Layout Plotly commun à tous les graphiques — cohérent avec les tokens de design de l'app. */
export const basePlotlyLayout: Partial<Layout> = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: { family: "var(--font-sans), system-ui, sans-serif", color: "hsl(215 16% 42%)", size: 12 },
  margin: { l: 48, r: 24, t: 24, b: 40 },
  xaxis: { gridcolor: "hsl(214 30% 91%)", zerolinecolor: "hsl(214 30% 91%)" },
  yaxis: { gridcolor: "hsl(214 30% 91%)", zerolinecolor: "hsl(214 30% 91%)" },
  legend: { orientation: "h", y: -0.2 },
};

export const basePlotlyConfig: Partial<Config> = {
  displayModeBar: false,
  responsive: true,
};

interface PlotlyChartProps {
  data: Data[];
  layout?: Partial<Layout>;
  config?: Partial<Config>;
  className?: string;
  height?: number;
}

export function PlotlyChart({ data, layout, config, className, height = 340 }: PlotlyChartProps) {
  return (
    <div className={cn("w-full", className)} style={{ height }}>
      <Plot
        data={data}
        layout={{ ...basePlotlyLayout, ...layout, height, autosize: true }}
        config={{ ...basePlotlyConfig, ...config }}
        style={{ width: "100%", height: "100%" }}
        useResizeHandler
      />
    </div>
  );
}
