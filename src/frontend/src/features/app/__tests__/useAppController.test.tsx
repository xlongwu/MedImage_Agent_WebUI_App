import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl, getHealth } from "../../../lib/api";
import { useAppController } from "../useAppController";

vi.mock("../../../lib/api", () => ({
  DEFAULT_API_BASE: "http://localhost",
  getApiBaseUrl: vi.fn(),
  getHealth: vi.fn(),
  sendAssistantMessage: vi.fn(),
}));

vi.mock("../../../hooks/useTasks", () => ({
  useTasks: () => ({
    reload: vi.fn(),
  }),
}));

describe("useAppController health", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    vi.mocked(getApiBaseUrl).mockResolvedValue("http://localhost");
  });

  it("marks the backend connected when health returns ok", async () => {
    vi.mocked(getHealth).mockResolvedValue({ status: "ok" });

    const { result } = renderHook(() => useAppController());

    await waitFor(() => expect(result.current.health).toBe(true));
    expect(result.current.apiError).toBe("");
    expect(getHealth).toHaveBeenCalledWith("http://localhost");
  });

  it("does not treat a non-ready health payload as connected", async () => {
    vi.mocked(getHealth).mockResolvedValue({ status: "starting" });

    const { result } = renderHook(() => useAppController());

    await waitFor(() => expect(result.current.health).toBe(false));
    expect(result.current.apiError).toBe("Backend health check returned a non-ready status.");
  });

  it("enters offline after three failed health attempts and recovers on retry", async () => {
    vi.mocked(getHealth).mockRejectedValue(new Error("connection refused"));

    const { result } = renderHook(() => useAppController());

    await waitFor(() => expect(result.current.health).toBe(false), { timeout: 2500 });
    expect(getHealth).toHaveBeenCalledTimes(3);
    expect(result.current.apiError).toContain("Backend disconnected");

    vi.mocked(getHealth).mockReset();
    vi.mocked(getHealth).mockResolvedValue({ status: "healthy" });

    await act(async () => {
      await result.current.checkHealth();
    });

    expect(result.current.health).toBe(true);
    expect(result.current.apiError).toBe("");
  });
});
