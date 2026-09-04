import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlgoParams } from "@/lib/api";

interface Props {
  value: AlgoParams;
  onChange: (value: AlgoParams) => void;
}

/** Formulaire de paramètres — un jeu de champs différent par algorithme, cf. run_clustering() côté backend. */
export function AlgoParamsForm({ value, onChange }: Props) {
  switch (value.algo_id) {
    case "kmeans":
      return (
        <div className="flex items-end gap-4">
          <div>
            <Label className="text-xs text-muted-foreground">Nombre de clusters (k)</Label>
            <Input
              type="number"
              min={2}
              value={value.k}
              onChange={(e) => onChange({ ...value, k: Number(e.target.value) })}
              className="w-28"
            />
          </div>
        </div>
      );

    case "dbscan":
      return (
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <Label className="text-xs text-muted-foreground">ε (rayon de voisinage)</Label>
            <Input
              type="number"
              min={0.01}
              step={0.1}
              value={value.eps}
              onChange={(e) => onChange({ ...value, eps: Number(e.target.value) })}
              className="w-28"
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">min_samples</Label>
            <Input
              type="number"
              min={2}
              value={value.min_samples}
              onChange={(e) => onChange({ ...value, min_samples: Number(e.target.value) })}
              className="w-28"
            />
          </div>
        </div>
      );

    case "agglomerative":
      return (
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <Label className="text-xs text-muted-foreground">Nombre de clusters (k)</Label>
            <Input
              type="number"
              min={2}
              value={value.k}
              onChange={(e) => onChange({ ...value, k: Number(e.target.value) })}
              className="w-28"
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Linkage</Label>
            <Select value={value.linkage} onValueChange={(v) => onChange({ ...value, linkage: v as typeof value.linkage })}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ward">ward</SelectItem>
                <SelectItem value="complete">complete</SelectItem>
                <SelectItem value="average">average</SelectItem>
                <SelectItem value="single">single</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      );

    case "gmm":
      return (
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <Label className="text-xs text-muted-foreground">Nombre de composantes (k)</Label>
            <Input
              type="number"
              min={2}
              value={value.k}
              onChange={(e) => onChange({ ...value, k: Number(e.target.value) })}
              className="w-28"
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Type de covariance</Label>
            <Select value={value.cov_type} onValueChange={(v) => onChange({ ...value, cov_type: v as typeof value.cov_type })}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="full">full</SelectItem>
                <SelectItem value="tied">tied</SelectItem>
                <SelectItem value="diag">diag</SelectItem>
                <SelectItem value="spherical">spherical</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      );

    case "meanshift":
      return (
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <Label className="text-xs text-muted-foreground">Bandwidth</Label>
            <Select
              value={value.bandwidth === "auto" ? "auto" : "manual"}
              onValueChange={(v) => onChange({ ...value, bandwidth: v === "auto" ? "auto" : 1 })}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Automatique (estimée)</SelectItem>
                <SelectItem value="manual">Manuelle</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {value.bandwidth !== "auto" && (
            <Input
              type="number"
              min={0.01}
              step={0.1}
              value={value.bandwidth}
              onChange={(e) => onChange({ ...value, bandwidth: Number(e.target.value) })}
              className="w-28"
            />
          )}
        </div>
      );

    default:
      return null;
  }
}
