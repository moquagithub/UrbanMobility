import { Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { AlgorithmInfo, AlgoId } from "@/lib/api";

export function AlgorithmPicker({
  algorithms,
  selected,
  onSelect,
}: {
  algorithms: AlgorithmInfo[];
  selected: AlgoId | null;
  onSelect: (algoId: AlgoId) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {algorithms.map((algo) => {
        const isSelected = selected === algo.algo_id;
        return (
          <button
            key={algo.algo_id}
            type="button"
            onClick={() => onSelect(algo.algo_id as AlgoId)}
            className={cn(
              "flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors",
              isSelected ? "border-primary bg-accent/50 ring-1 ring-primary" : "border-border hover:border-primary/40 hover:bg-accent/20"
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">{algo.icon}</span>
                <span className="text-sm font-semibold text-foreground">{algo.name}</span>
              </div>
              {isSelected ? (
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                  <Check className="h-3 w-3" />
                </span>
              ) : (
                <Badge variant="outline">{algo.score}</Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground">{algo.tagline}</p>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground">
              <span>
                <span className="font-medium text-foreground">Complexité :</span> {algo.complexity}
              </span>
              <span>
                <span className="font-medium text-foreground">Paramètres :</span> {algo.params}
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              <span className="font-medium text-foreground">Idéal si </span>
              {algo.best_for}
            </p>
          </button>
        );
      })}
    </div>
  );
}
