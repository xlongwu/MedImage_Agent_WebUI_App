import { afterEach, describe, expect, it, vi } from "vitest";

import { getProjectAgentSettings, updateProjectAgentSettings } from "../agentSettings";

describe("agent settings API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("binds project settings reads and updates to the encoded project route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            schema_version: 1,
            project_id: "project / 1",
            cpu_policy: "auto",
            compute_policy: "auto",
            default_atlas: null,
            default_template: null,
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
    );

    await getProjectAgentSettings("http://api", "project / 1");
    await updateProjectAgentSettings("http://api", "project / 1", {
      default_atlas: null,
      default_template: null,
      cpu_policy: "auto",
      compute_policy: "auto",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://api/api/projects/project%20%2F%201/agent-settings",
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "PUT" });
  });
});
