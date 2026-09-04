import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { usePollingJob } from "@/lib/usePollingJob";
import * as api from "@/lib/api";

describe("usePollingJob()", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does nothing when jobId is null", () => {
    const spy = vi.spyOn(api, "getJobStatus");
    renderHook(() => usePollingJob(null));
    expect(spy).not.toHaveBeenCalled();
  });

  it("polls repeatedly while the job is pending/running, then stops once completed", async () => {
    const statuses = ["pending", "running", "running", "completed"] as const;
    let call = 0;
    vi.spyOn(api, "getJobStatus").mockImplementation(async () => {
      const status = statuses[Math.min(call, statuses.length - 1)];
      call += 1;
      return {
        job_id: "job-1",
        job_type: "clustering",
        dataset_id: "ds-1",
        status,
        created_at: "now",
        started_at: null,
        finished_at: status === "completed" ? "now" : null,
        result: status === "completed" ? ({ ok: true } as any) : null,
        error: null,
      };
    });

    const { result } = renderHook(() => usePollingJob("job-1", 1000));

    await waitFor(() => expect(result.current.job?.status).toBe("pending"));

    await vi.advanceTimersByTimeAsync(1000);
    await waitFor(() => expect(result.current.job?.status).toBe("running"));

    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(1000);
    await waitFor(() => expect(result.current.job?.status).toBe("completed"));

    expect(result.current.job?.result).toEqual({ ok: true });

    // Ne doit plus re-poller après complétion.
    const callsAtCompletion = call;
    await vi.advanceTimersByTimeAsync(5000);
    expect(call).toBe(callsAtCompletion);
  });

  it("surfaces a network error message", async () => {
    vi.spyOn(api, "getJobStatus").mockRejectedValue(new api.ApiError(500, "Erreur serveur."));
    const { result } = renderHook(() => usePollingJob("job-err"));
    await waitFor(() => expect(result.current.error).toBe("Erreur serveur."));
  });
});
