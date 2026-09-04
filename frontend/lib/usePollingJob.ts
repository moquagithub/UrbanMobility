"use client";

import { useEffect, useRef, useState } from "react";
import { getJobStatus, ApiError, JobStatusResponse } from "@/lib/api";

interface UsePollingJobState<TResult> {
  job: JobStatusResponse<TResult> | null;
  error: string | null;
  elapsedSeconds: number;
}

/**
 * Interroge GET /ml/jobs/{jobId} à intervalle régulier jusqu'à 'completed'/'failed'.
 * Utilisé pour les tâches longues (clustering, k-selection) déclenchées en
 * arrière-plan côté backend (FastAPI BackgroundTasks, DQE-8) — l'UI reste
 * réactive pendant l'attente : aucun appel bloquant, juste un polling léger.
 */
export function usePollingJob<TResult>(jobId: string | null, intervalMs = 1200): UsePollingJobState<TResult> {
  const [job, setJob] = useState<JobStatusResponse<TResult> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const startRef = useRef<number>(Date.now());

  useEffect(() => {
    if (!jobId) return;
    const currentJobId = jobId;
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;
    startRef.current = Date.now();

    const tick = () => {
      setElapsedSeconds(Math.floor((Date.now() - startRef.current) / 1000));
    };
    const elapsedInterval = setInterval(tick, 1000);

    async function poll() {
      try {
        const result = await getJobStatus<TResult>(currentJobId);
        if (cancelled) return;
        setJob(result);
        if (result.status === "pending" || result.status === "running") {
          timeoutId = setTimeout(poll, intervalMs);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Erreur réseau lors du suivi de la tâche.");
        }
      }
    }

    poll();

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
      clearInterval(elapsedInterval);
    };
  }, [jobId, intervalMs]);

  return { job, error, elapsedSeconds };
}
