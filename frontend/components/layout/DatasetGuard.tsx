"use client";

import Link from "next/link";
import { FileQuestion, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDataset } from "@/lib/DatasetContext";

/**
 * Enveloppe toutes les pages sous /datasets/[id]/... : affiche un état de
 * chargement puis un message clair si le dataset n'existe pas (404), sans
 * quoi laisse passer les pages normalement.
 *
 * L'application est en accès libre — aucune redirection vers une page de
 * connexion, aucun cas 401/403 à traiter.
 */
export function DatasetGuard({ children }: { children: React.ReactNode }) {
  const { loading, error, errorStatus, overview } = useDataset();

  if (loading) {
    return (
      <div className="flex min-h-screen flex-1 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (errorStatus === 404) {
    return (
      <div className="flex min-h-screen flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
        <FileQuestion className="h-8 w-8 text-muted-foreground" />
        <h1 className="text-lg font-semibold text-foreground">Dataset introuvable</h1>
        <p className="max-w-sm text-sm text-muted-foreground">{error}</p>
        <Button asChild variant="outline" size="sm">
          <Link href="/">Retour à l&apos;accueil</Link>
        </Button>
      </div>
    );
  }

  if (!overview) {
    return null; // autre erreur : la page elle-même affichera l'ErrorState via son propre appel
  }

  return <>{children}</>;
}
