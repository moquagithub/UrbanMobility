import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getOverview, getMissing, applyRecommendations, ApiError } from "@/lib/api";

const originalFetch = global.fetch;

describe("lib/api", () => {
  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("getOverview() returns parsed JSON on success", async () => {
    const payload = { dataset_id: "abc123", filename: "test.csv", nb_lignes: 10 };
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    }) as unknown as typeof fetch;

    const result = await getOverview("abc123");
    expect(result).toEqual(payload);
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/datasets/abc123/overview"));
  });

  it("getMissing() throws ApiError with the backend detail message on failure", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({ detail: "Dataset 'xyz' introuvable." }),
    }) as unknown as typeof fetch;

    await expect(getMissing("xyz")).rejects.toThrow("Dataset 'xyz' introuvable.");
    await expect(getMissing("xyz")).rejects.toBeInstanceOf(ApiError);
  });

  it("getMissing() falls back to statusText when the error body isn't JSON", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => {
        throw new Error("not json");
      },
    }) as unknown as typeof fetch;

    try {
      await getMissing("xyz");
      expect.unreachable("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(500);
      expect((err as ApiError).message).toBe("Internal Server Error");
    }
  });

  it("applyRecommendations() POSTs the selected action_ids as JSON", async () => {
    const payload = { dataset_id: "d1", new_dataset_id: "d2", journal: [], comparaison: {}, apercu: [] };
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => payload }) as unknown as typeof fetch;

    await applyRecommendations("d1", ["drop_duplicates", "standardize"]);

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/datasets/d1/recommendations/apply"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action_ids: ["drop_duplicates", "standardize"] }),
      })
    );
  });
});
