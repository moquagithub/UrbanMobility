import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MetricsCards } from "@/components/ml/MetricsCards";

describe("<MetricsCards />", () => {
  it("renders all six metrics with formatted values", () => {
    render(
      <MetricsCards
        metrics={{ n_clusters: 4, n_noise: 12, n_total: 300, silhouette: 0.6543, calinski: 812.456, davies: 0.789 }}
      />
    );
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("0.654")).toBeInTheDocument();
    expect(screen.getByText("812.5")).toBeInTheDocument();
    expect(screen.getByText("0.789")).toBeInTheDocument();
  });

  it("shows a dash for null metrics (e.g. a single cluster)", () => {
    render(<MetricsCards metrics={{ n_clusters: 1, n_noise: 0, n_total: 50, silhouette: null, calinski: null, davies: null }} />);
    expect(screen.getAllByText("—")).toHaveLength(3);
  });
});
