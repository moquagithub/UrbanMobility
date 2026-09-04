import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import UploadForm from "@/components/UploadForm";
import * as api from "@/lib/api";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

describe("<UploadForm />", () => {
  beforeEach(() => {
    pushMock.mockClear();
  });

  it("shows the dropzone prompt initially", () => {
    render(<UploadForm />);
    expect(screen.getByText(/Glissez un fichier CSV/i)).toBeInTheDocument();
  });

  it("redirects to the dataset overview page after a successful upload", async () => {
    vi.spyOn(api, "uploadCsv").mockResolvedValue({
      dataset_id: "ds-42",
      filename: "test.csv",
      n_lignes: 10,
      n_colonnes: 3,
      separateur_detecte: ",",
      message: "ok",
    });

    render(<UploadForm />);
    const file = new File(["a,b\n1,2"], "test.csv", { type: "text/csv" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/datasets/ds-42/overview"));
  });

  it("shows an error message when the upload fails", async () => {
    vi.spyOn(api, "uploadCsv").mockRejectedValue(new api.ApiError(422, "Fichier CSV invalide."));

    render(<UploadForm />);
    const file = new File(["not,a,csv"], "bad.csv", { type: "text/csv" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(screen.getByText("Fichier CSV invalide.")).toBeInTheDocument());
    expect(pushMock).not.toHaveBeenCalled();
  });
});
