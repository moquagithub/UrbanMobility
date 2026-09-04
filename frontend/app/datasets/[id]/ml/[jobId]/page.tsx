"use client";

import Link from "next/link";
import { ArrowLeft, RotateCcw } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState } from "@/components/shared/States";
import { Button } from "@/components/ui/button";
import { JobProgress } from "@/components/ml/JobProgress";
import { ClusteringResults } from "@/components/ml/ClusteringResults";
import { usePollingJob } from "@/lib/usePollingJob";
import { ClusteringResult } from "@/lib/api";

export default function MLResultsPage({ params }: { params: { id: string; jobId: string } }) {
  const { job, error, elapsedSeconds } = usePollingJob<ClusteringResult>(params.jobId);

  return (
    <div>
      <PageHeader
        title={job?.result ? `Résultats — ${job.result.algo_name}` : "Résultats du clustering"}
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link href={`/datasets/${params.id}/ml`}>
              <ArrowLeft className="h-4 w-4" />
              Nouvelle configuration
            </Link>
          </Button>
        }
      />

      {error && <ErrorState message={error} />}

      {!job && !error && <JobProgress status="pending" elapsedSeconds={elapsedSeconds} />}

      {job && (job.status === "pending" || job.status === "running") && (
        <JobProgress status={job.status} elapsedSeconds={elapsedSeconds} />
      )}

      {job?.status === "failed" && (
        <div className="space-y-4">
          <ErrorState title="Le clustering a échoué" message={job.error ?? "Erreur inconnue."} />
          <Button asChild variant="outline">
            <Link href={`/datasets/${params.id}/ml`}>
              <RotateCcw className="h-4 w-4" />
              Retour à la configuration
            </Link>
          </Button>
        </div>
      )}

      {job?.status === "completed" && job.result && <ClusteringResults result={job.result} />}
    </div>
  );
}
