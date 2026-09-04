"use client";

import { createContext, useContext } from "react";
import { getOverview, OverviewResponse } from "@/lib/api";
import { useApiResource } from "@/lib/useApiResource";

interface DatasetContextValue {
  datasetId: string;
  overview: OverviewResponse | null;
  loading: boolean;
  error: string | null;
  errorStatus: number | null;
  refetch: () => void;
}

const DatasetContext = createContext<DatasetContextValue | null>(null);

export function DatasetProvider({ datasetId, children }: { datasetId: string; children: React.ReactNode }) {
  const { data, loading, error, errorStatus, refetch } = useApiResource(() => getOverview(datasetId), [datasetId]);

  return (
    <DatasetContext.Provider value={{ datasetId, overview: data, loading, error, errorStatus, refetch }}>
      {children}
    </DatasetContext.Provider>
  );
}

/** Accès au dataset courant (id + vue d'ensemble déjà chargée) depuis n'importe quelle page EDA. */
export function useDataset(): DatasetContextValue {
  const ctx = useContext(DatasetContext);
  if (!ctx) throw new Error("useDataset() doit être utilisé sous <DatasetProvider>.");
  return ctx;
}
