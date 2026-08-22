import { beforeEach, describe, expect, it, vi } from "vitest";

import { getAgentOperationalSummary } from "../agentOperations";

const fetchMock = vi.fn();

describe("agent operations API", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
  });

  it("requests the bounded project-scoped seven-day projection", async () => {
    await getAgentOperationalSummary("http://localhost", "project/one");

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/projects/project%2Fone/agent-operations/summary?window_hours=168",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method ?? "GET").toBe("GET");
  });
});
