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
            multi_agent_consent: {
              schema_version: 1,
              project_id: "project / 1",
              enabled: false,
              consent_epoch: 0,
              consent_hash: "f".repeat(64),
              allowed_data_categories: [],
              max_model_calls: 6,
              max_input_tokens: 16384,
              updated_at: null,
            },
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
      multi_agent_consent_enabled: true,
      multi_agent_max_model_calls: 6,
      multi_agent_max_input_tokens: 16384,
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://api/api/projects/project%20%2F%201/agent-settings",
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "PUT" });
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
      multi_agent_consent_enabled: true,
      multi_agent_max_model_calls: 6,
      multi_agent_max_input_tokens: 16384,
    });
  });
});
