import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import { getAgentOperationalSummary } from "../../../lib/api/agentOperations";
import { AgentOperationalHealthCard } from "../components/AgentOperationalHealthCard";

vi.mock("../../../lib/api/agentOperations", () => ({
  getAgentOperationalSummary: vi.fn(),
}));

describe("AgentOperationalHealthCard", () => {
  beforeEach(() => {
    vi.mocked(getAgentOperationalSummary).mockReset();
    vi.mocked(getAgentOperationalSummary).mockResolvedValue({
      schema_version: 1,
      project_id: "project-1",
      window_started_at: "2026-08-09T00:00:00Z",
      generated_at: "2026-08-16T00:00:00Z",
      truncated: true,
      task_counts: { total: 3, WAITING_FOR_APPROVAL: 1 },
      model_call_counts: { success: 2, failure: 1, unknown: 0 },
      provider_failure_counts: { AGENT_HARNESS_PROVIDER_UNAVAILABLE: 1 },
      scheduler_counts: {},
      approval_counts: { waiting: 1 },
      gateway_counts: {},
      sandbox_counts: {},
      memory_status: "healthy",
      latency_ms: { model_call_p50: 10, model_call_p95: 20 },
      attention: [
        {
          code: "AGENT_OP_PROVIDER_FAILURES",
          severity: "warning",
          count: 1,
          related_ids: ["call-1"],
        },
      ],
    });
  });

  it("renders only the read-only structured health projection", async () => {
    render(
      <I18nProvider locale="en">
        <AgentOperationalHealthCard advancedMode baseUrl="http://localhost" projectId="project-1" />
      </I18nProvider>,
    );

    await waitFor(() => expect(getAgentOperationalSummary).toHaveBeenCalledOnce());
    expect(
      screen.getByText("3 tasks in the last seven days; 1 waiting for approval."),
    ).toBeInTheDocument();
    expect(screen.getByText("Model calls — 2 succeeded, 1 failed, 0 unknown.")).toBeInTheDocument();
    expect(screen.getByText("AGENT_OP_PROVIDER_FAILURES: 1")).toBeInTheDocument();
    expect(
      screen.getByText("The operational view is truncated to the safe task limit."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText("call-1")).not.toBeInTheDocument();
  });
});
