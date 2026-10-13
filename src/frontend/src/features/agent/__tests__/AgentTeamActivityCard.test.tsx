import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import { listAgentTaskTeamEvents, listAgentTaskTeamFindings } from "../../../lib/api/agentTasks";
import type { AgentTaskTeamSummary } from "../../../lib/types/agentTask";
import { AgentTeamActivityCard } from "../components/AgentTeamActivityCard";

vi.mock("../../../lib/api/agentTasks", () => ({
  listAgentTaskTeamEvents: vi.fn(),
  listAgentTaskTeamFindings: vi.fn(),
}));

const summary: AgentTaskTeamSummary = {
  schema_version: 1,
  team_id: "team-1",
  team_epoch: 1,
  status: "COMPLETED",
  roles: [
    {
      reviewer_kind: "science",
      role_id: "science_reviewer.v1",
      status: "COMPLETED",
      error_code: null,
    },
  ],
  required_roles: ["science", "safety", "completeness"],
  completed_roles: ["science"],
  failed_roles: [],
  blocking_finding_count: 0,
  warning_finding_count: 1,
  conflict_count: 0,
  model_call_count: 3,
  budget_used: 3,
  budget_limit: 4,
  input_tokens: 120,
  output_tokens: 30,
  latency_ms: 500,
  started_at: "2026-10-13T00:00:00Z",
  updated_at: "2026-10-13T00:01:00Z",
  completed_at: "2026-10-13T00:01:00Z",
  advisory_hash: "a".repeat(64),
  attention_code: null,
};

describe("AgentTeamActivityCard", () => {
  it("offers standard planning as a separate task after a Team failure", () => {
    const continueSingle = vi.fn().mockResolvedValue(undefined);
    render(
      <I18nProvider locale="en">
        <AgentTeamActivityCard
          baseUrl="http://api"
          onContinueSingle={continueSingle}
          projectId="project-1"
          summary={{ ...summary, status: "NEEDS_ATTENTION" }}
          taskId="task-1"
        />
      </I18nProvider>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Start a separate standard-planning task" }),
    );
    expect(continueSingle).toHaveBeenCalledOnce();
  });

  it.each(["en", "zh-CN"] as const)("shows localized review activity in %s", async (locale) => {
    vi.mocked(listAgentTaskTeamFindings).mockResolvedValue({
      schema_version: 1,
      items: [
        {
          finding_id: "finding-1",
          reviewer_kind: "science",
          severity: "warning",
          code: "SCIENCE_EVIDENCE_GAP",
          message_key: "agent.review.science_gap",
          input_refs: ["reviewed_plan"],
        },
      ],
      next_cursor: null,
    });
    vi.mocked(listAgentTaskTeamEvents).mockResolvedValue({
      schema_version: 1,
      items: [
        {
          event_id: "event-1",
          team_id: "team-1",
          event_type: "MODEL_CALL_COMPLETED",
          occurred_at: "2026-10-13T00:01:00Z",
          source: "worker",
          message: { code: "agent.team.event.review_completed", params: {} },
        },
      ],
      next_cursor: null,
    });

    render(
      <I18nProvider locale={locale}>
        <AgentTeamActivityCard
          baseUrl="http://api"
          projectId="project-1"
          summary={summary}
          taskId="task-1"
        />
      </I18nProvider>,
    );

    expect(
      screen.getByText(locale === "en" ? "Independent planning review" : "独立规划评审"),
    ).toBeVisible();
    expect(screen.getByText("3 / 4")).toBeVisible();
    fireEvent.click(
      screen.getByText(locale === "en" ? "Show findings and activity" : "显示发现和活动记录"),
    );
    expect(await screen.findByText("SCIENCE_EVIDENCE_GAP")).toBeVisible();
    expect(
      await screen.findByText(
        locale === "en" ? "A reviewer completed work." : "一名评审者已完成工作。",
      ),
    ).toBeVisible();
  });
});
