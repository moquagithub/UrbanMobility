import { MetricsCards } from "@/components/ml/MetricsCards";
import { ClusterSizesChart } from "@/components/ml/ClusterSizesChart";
import { ProjectionScatter } from "@/components/ml/ProjectionScatter";
import { ClusterProfilesRadar } from "@/components/ml/ClusterProfilesRadar";
import { DiscriminantFeaturesChart } from "@/components/ml/DiscriminantFeaturesChart";
import { SilhouettePlot } from "@/components/ml/SilhouettePlot";
import { ClusterStatsTable } from "@/components/ml/ClusterStatsTable";
import { InterpretationPanel } from "@/components/ml/InterpretationPanel";
import { ClusteringResult } from "@/lib/api";

export function ClusteringResults({ result }: { result: ClusteringResult }) {
  return (
    <div className="space-y-6">
      <MetricsCards metrics={result.metrics} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ProjectionScatter pca={result.pca_2d} tsne={result.tsne_2d} />
        </div>
        <ClusterSizesChart sizes={result.cluster_sizes} />
      </div>

      {result.cluster_profiles.disponible && <ClusterProfilesRadar profiles={result.cluster_profiles} />}

      <div className="grid gap-4 lg:grid-cols-2">
        <DiscriminantFeaturesChart features={result.top_discriminant_features} />
        {result.silhouette_plot && <SilhouettePlot silhouette={result.silhouette_plot} />}
      </div>

      <InterpretationPanel interpretation={result.interpretation} />

      <ClusterStatsTable stats={result.cluster_stats} />
    </div>
  );
}
