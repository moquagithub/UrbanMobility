import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { DiscriminantFeature } from "@/lib/api";

export function DiscriminantFeaturesChart({ features }: { features: DiscriminantFeature[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Variables les plus discriminantes</CardTitle>
        <CardDescription>Score F (ANOVA) — plus il est élevé, plus la variable sépare les clusters</CardDescription>
      </CardHeader>
      <CardContent>
        <PlotlyChart
          height={Math.max(240, features.length * 30)}
          data={[
            {
              type: "bar",
              orientation: "h",
              x: [...features].reverse().map((f) => f.f_score),
              y: [...features].reverse().map((f) => f.feature),
              marker: { color: "hsl(174 84% 24%)" },
            },
          ]}
          layout={{ yaxis: { automargin: true, tickfont: { size: 10 } }, xaxis: { title: { text: "Score F" } } }}
        />
      </CardContent>
    </Card>
  );
}
