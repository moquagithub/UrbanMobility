"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { UploadCloud, Loader2 } from "lucide-react";
import { uploadCsv, ApiError } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertCircle } from "lucide-react";

export default function UploadForm() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File) {
    setError(null);
    setLoading(true);
    setFileName(file.name);
    try {
      const result = await uploadCsv(file);
      router.push(`/datasets/${result.dataset_id}/overview`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erreur réseau inattendue — le backend est-il démarré ?");
      setLoading(false);
    }
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  return (
    <div className="w-full max-w-xl">
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
        onClick={() => !loading && inputRef.current?.click()}
        className="group flex cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed border-border bg-card px-8 py-14 text-center transition-colors hover:border-primary/50 hover:bg-accent/30"
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
        />
        {loading ? (
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        ) : (
          <UploadCloud className="h-8 w-8 text-muted-foreground transition-colors group-hover:text-primary" />
        )}
        <p className="text-sm font-medium text-foreground">
          {loading ? `Analyse de « ${fileName} » en cours…` : "Glissez un fichier CSV, ou cliquez pour parcourir"}
        </p>
        <p className="text-xs text-muted-foreground">
          Anonymisation automatique des colonnes et valeurs sensibles (PII) au chargement
        </p>
      </div>

      {error && (
        <Alert variant="destructive" className="mt-4">
          <AlertCircle />
          <AlertTitle>Échec du chargement</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}
