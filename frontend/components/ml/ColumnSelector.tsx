import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScalerType, EncodingPlanItem } from "@/lib/api";

interface ColumnSelectorProps {
  numCols: string[];
  catCols: string[];
  selectedNumCols: Set<string>;
  selectedCatCols: Set<string>;
  onToggleNum: (col: string) => void;
  onToggleCat: (col: string) => void;
  scalerType: ScalerType;
  onScalerChange: (v: ScalerType) => void;
  usePca: boolean;
  onUsePcaChange: (v: boolean) => void;
  pcaVariance: number;
  onPcaVarianceChange: (v: number) => void;
  computeTsne: boolean;
  onComputeTsneChange: (v: boolean) => void;
  encodingPlan?: Record<string, EncodingPlanItem>;
}

export function ColumnSelector({
  numCols,
  catCols,
  selectedNumCols,
  selectedCatCols,
  onToggleNum,
  onToggleCat,
  scalerType,
  onScalerChange,
  usePca,
  onUsePcaChange,
  pcaVariance,
  onPcaVarianceChange,
  computeTsne,
  onComputeTsneChange,
  encodingPlan,
}: ColumnSelectorProps) {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          Variables numériques ({selectedNumCols.size}/{numCols.length})
        </Label>
        <div className="mt-2 max-h-56 space-y-1 overflow-y-auto rounded-md border border-border p-2">
          {numCols.map((col) => (
            <label key={col} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-accent/40">
              <Checkbox checked={selectedNumCols.has(col)} onCheckedChange={() => onToggleNum(col)} />
              <span className="font-mono text-xs">{col}</span>
            </label>
          ))}
          {numCols.length === 0 && <p className="p-2 text-xs text-muted-foreground">Aucune variable numérique.</p>}
        </div>
      </div>

      <div>
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          Variables catégorielles à encoder ({selectedCatCols.size}/{catCols.length})
        </Label>
        <div className="mt-2 max-h-56 space-y-1 overflow-y-auto rounded-md border border-border p-2">
          {catCols.map((col) => {
            const hint = encodingPlan?.[col];
            return (
              <label key={col} className="flex cursor-pointer items-start gap-2 rounded px-2 py-1 text-sm hover:bg-accent/40">
                <Checkbox checked={selectedCatCols.has(col)} onCheckedChange={() => onToggleCat(col)} className="mt-0.5" />
                <span className="flex-1">
                  <span className="font-mono text-xs">{col}</span>
                  {hint && (
                    <span className="ml-2 text-[11px] text-muted-foreground">
                      {hint.methode.replace(/_/g, " ")} · {hint.n_modalites} modalités
                    </span>
                  )}
                </span>
              </label>
            );
          })}
          {catCols.length === 0 && <p className="p-2 text-xs text-muted-foreground">Aucune variable catégorielle.</p>}
        </div>
      </div>

      <div className="space-y-3">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">Standardisation</Label>
        <Select value={scalerType} onValueChange={(v) => onScalerChange(v as ScalerType)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="standard">StandardScaler (Z-score)</SelectItem>
            <SelectItem value="robust">RobustScaler (médiane/IQR)</SelectItem>
            <SelectItem value="minmax">MinMaxScaler (0-1)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={usePca} onCheckedChange={(v) => onUsePcaChange(Boolean(v))} />
          Réduire par PCA avant clustering
        </label>
        {usePca && (
          <div className="flex items-center gap-2">
            <Label className="text-xs text-muted-foreground">Variance conservée</Label>
            <Input
              type="number"
              min={0.5}
              max={0.99}
              step={0.01}
              value={pcaVariance}
              onChange={(e) => onPcaVarianceChange(Number(e.target.value))}
              className="w-24"
            />
          </div>
        )}
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={computeTsne} onCheckedChange={(v) => onComputeTsneChange(Boolean(v))} />
          Calculer aussi la projection t-SNE
          <Badge variant="warning">plus lent</Badge>
        </label>
      </div>
    </div>
  );
}
