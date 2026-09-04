const PALETTE = [
  "hsl(174 84% 24%)", // teal (accent de marque)
  "hsl(38 92% 42%)", // amber
  "hsl(262 60% 55%)", // violet
  "hsl(0 72% 46%)", // rouge
  "hsl(200 80% 45%)", // bleu
  "hsl(142 66% 30%)", // vert
  "hsl(330 65% 50%)", // rose
  "hsl(30 80% 45%)", // orange
];

const NOISE_COLOR = "hsl(215 16% 70%)";

/** Couleur stable par cluster_id — cohérente sur toutes les visualisations (scatter, radar, tailles, silhouette). */
export function clusterColor(clusterId: number): string {
  if (clusterId < 0) return NOISE_COLOR;
  return PALETTE[clusterId % PALETTE.length];
}
