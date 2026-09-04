import { StatCard } from "@/components/shared/StatCard";
import { ClusteringMetrics } from "@/lib/api";

function fmt(v: number | null, digits = 3): string {
  return v === null ? "—" : v.toFixed(digits);
}

export function MetricsCards({ metrics }: { metrics: ClusteringMetrics }) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
      <StatCard label="Clusters" value={metrics.n_clusters} />
      <StatCard label="Bruit" value={metrics.n_noise} />
      <StatCard label="Observations" value={metrics.n_total} />
      <StatCard label="Silhouette" value={fmt(metrics.silhouette)} />
      <StatCard label="Calinski-Harabasz" value={fmt(metrics.calinski, 1)} />
      <StatCard label="Davies-Bouldin" value={fmt(metrics.davies)} />
    </div>
  );
}
