import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string | number;
  accentClassName?: string;
}

export function StatCard({ label, value, accentClassName }: StatCardProps) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className={cn("mt-1 font-mono text-xl font-semibold tabular-nums text-foreground", accentClassName)}>
          {value}
        </div>
      </CardContent>
    </Card>
  );
}
