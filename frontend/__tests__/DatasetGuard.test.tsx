import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { DatasetGuard } from "@/components/layout/DatasetGuard";
import * as datasetContext from "@/lib/DatasetContext";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/datasets/abc123/overview",
}));

beforeEach(() => {
  pushMock.mockClear();
  replaceMock.mockClear();
});

function mockUseDataset(overrides: Partial<ReturnType<typeof datasetContext.useDataset>>) {
  vi.spyOn(datasetContext, "useDataset").mockReturnValue({
    datasetId: "abc123",
    overview: null,
    loading: false,
    error: null,
    errorStatus: null,
    refetch: vi.fn(),
    ...overrides,
  });
}

describe("<DatasetGuard />", () => {
  it("shows a loading spinner while the overview is loading", () => {
    mockUseDataset({ loading: true });
    const { container } = render(
      <DatasetGuard>
        <div>contenu protégé</div>
      </DatasetGuard>
    );
    expect(container.querySelector(".animate-spin")).toBeTruthy();
    expect(screen.queryByText("contenu protégé")).not.toBeInTheDocument();
  });

  it("shows 'Dataset introuvable' on a 404", () => {
    mockUseDataset({ errorStatus: 404, error: "Dataset introuvable." });
    render(
      <DatasetGuard>
        <div>contenu protégé</div>
      </DatasetGuard>
    );
    expect(screen.getByText("Dataset introuvable")).toBeInTheDocument();
  });

  it("renders children once the overview loads successfully", () => {
    mockUseDataset({ overview: { dataset_id: "abc123" } as any });
    render(
      <DatasetGuard>
        <div>contenu protégé</div>
      </DatasetGuard>
    );
    expect(screen.getByText("contenu protégé")).toBeInTheDocument();
  });
});
