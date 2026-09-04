"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";

interface UseApiResourceState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** Code HTTP de l'erreur, si applicable (ex. 404) — utile pour afficher un message dédié. */
  errorStatus: number | null;
  refetch: () => void;
}

/**
 * Hook générique de récupération de données : gère loading/error/data de façon
 * uniforme sur toutes les pages EDA, pour éviter de dupliquer ce boilerplate
 * neuf fois. `deps` déclenche un refetch (ex. changement de dataset_id ou de
 * colonne sélectionnée).
 */
export function useApiResource<T>(fetcher: () => Promise<T>, deps: React.DependencyList): UseApiResourceState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setErrorStatus(null);

    fetcherRef
      .current()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Erreur réseau inattendue.");
          setErrorStatus(err instanceof ApiError ? err.status : null);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadToken]);

  const refetch = useCallback(() => setReloadToken((t) => t + 1), []);

  return { data, loading, error, errorStatus, refetch };
}
