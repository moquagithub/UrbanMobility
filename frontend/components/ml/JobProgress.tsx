import { Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

const STATUS_LABEL: Record<string, string> = {
  pending: "En attente de démarrage…",
  running: "Calcul en cours…",
};

/**
 * Indicateur de progression pour les tâches longues (clustering, k-selection).
 * Le backend n'expose pas de pourcentage précis (BackgroundTasks + store en
 * mémoire, cf. DQE-8) : une barre indéterminée + un compteur de temps écoulé
 * donnent un retour honnête plutôt qu'un pourcentage inventé.
 */
export function JobProgress({
  status,
  elapsedSeconds,
  algoName,
}: {
  status: "pending" | "running";
  elapsedSeconds: number;
  algoName?: string;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-4 py-16">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
        <div className="text-center">
          <p className="text-sm font-medium text-foreground">
            {STATUS_LABEL[status]} {algoName && `(${algoName})`}
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{elapsedSeconds}s écoulées</p>
        </div>
        <div className="h-1.5 w-64 overflow-hidden rounded-full bg-muted">
          <div className="h-full w-1/3 animate-indeterminate rounded-full bg-primary" />
        </div>
        <p className="max-w-sm text-center text-xs text-muted-foreground">
          Vous pouvez naviguer vers une autre page — le calcul continue en arrière-plan et le résultat sera disponible à votre retour ici.
        </p>
      </CardContent>
    </Card>
  );
}
