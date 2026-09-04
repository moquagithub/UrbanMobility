import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatCard } from "@/components/shared/StatCard";

describe("<StatCard />", () => {
  it("renders the label and value", () => {
    render(<StatCard label="Lignes" value={1234} />);
    expect(screen.getByText("Lignes")).toBeInTheDocument();
    expect(screen.getByText("1234")).toBeInTheDocument();
  });

  it("renders string values as-is", () => {
    render(<StatCard label="Complétude" value="87.5 %" />);
    expect(screen.getByText("87.5 %")).toBeInTheDocument();
  });

  it("applies a custom accent class when provided", () => {
    render(<StatCard label="Doublons" value={3} accentClassName="text-destructive" />);
    expect(screen.getByText("3")).toHaveClass("text-destructive");
  });
});
