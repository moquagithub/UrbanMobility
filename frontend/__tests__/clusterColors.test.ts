import { describe, it, expect } from "vitest";
import { clusterColor } from "@/components/ml/clusterColors";

describe("clusterColor()", () => {
  it("returns a distinct muted color for noise (cluster_id -1)", () => {
    expect(clusterColor(-1)).toBe("hsl(215 16% 70%)");
  });

  it("returns consistent colors for the same cluster id", () => {
    expect(clusterColor(2)).toBe(clusterColor(2));
  });

  it("returns different colors for different low cluster ids", () => {
    const colors = new Set([0, 1, 2, 3].map(clusterColor));
    expect(colors.size).toBe(4);
  });

  it("wraps around the palette for cluster ids beyond the palette size", () => {
    expect(clusterColor(0)).toBe(clusterColor(8));
  });
});
