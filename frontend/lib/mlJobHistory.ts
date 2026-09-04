"use client";

export interface MLJobHistoryEntry {
  job_id: string;
  algo_id: string;
  job_type: "clustering" | "k_selection";
  launched_at: string;
}

function storageKey(datasetId: string): string {
  return `ml-job-history:${datasetId}`;
}

/** Historique des tâches ML lancées dans cet onglet — perdu à la fermeture (sessionStorage), pas une source de vérité (le backend ne liste pas les jobs par dataset). */
export function getJobHistory(datasetId: string): MLJobHistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(storageKey(datasetId));
    return raw ? (JSON.parse(raw) as MLJobHistoryEntry[]) : [];
  } catch {
    return [];
  }
}

export function addJobToHistory(datasetId: string, entry: MLJobHistoryEntry): void {
  if (typeof window === "undefined") return;
  const current = getJobHistory(datasetId);
  const next = [entry, ...current].slice(0, 10);
  window.sessionStorage.setItem(storageKey(datasetId), JSON.stringify(next));
}
