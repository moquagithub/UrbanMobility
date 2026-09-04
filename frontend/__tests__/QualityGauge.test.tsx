import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QualityGauge } from "@/components/shared/QualityGauge";

describe("<QualityGauge />", () => {
  it("displays the rounded value", () => {
    render(<QualityGauge value={87.6} />);
    expect(screen.getByText("88")).toBeInTheDocument();
  });

  it("clamps values above 100", () => {
    render(<QualityGauge value={150} />);
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("clamps negative values to 0", () => {
    render(<QualityGauge value={-20} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("shows the optional label", () => {
    render(<QualityGauge value={90} label="complétude" />);
    expect(screen.getByText("complétude")).toBeInTheDocument();
  });

  it("uses the destructive color below the warn threshold", () => {
    render(<QualityGauge value={30} thresholds={{ good: 80, warn: 50 }} />);
    expect(screen.getByText("30")).toHaveClass("text-destructive");
  });

  it("uses the success color at or above the good threshold", () => {
    render(<QualityGauge value={95} thresholds={{ good: 80, warn: 50 }} />);
    expect(screen.getByText("95")).toHaveClass("text-success");
  });
});
