import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import { AgentAttentionDialog, useAgentAttentionDialog } from "../components/AgentAttentionDialog";
import type { AgentTaskController } from "../useAgentTaskController";

function task(projectId = "project-1"): AgentTaskResponse {
  return {
    schema_version: 1,
    task_id: "task-1",
    project_id: projectId,
    state: "waiting_for_user",
    outcome: null,
    goal_summary: "Generate FC",
    current_action: "Awaiting a scientific decision",
    next_action: {
      type: "answer_science_decision",
      title: "Answer decision",
      description: null,
      requires_user: true,
      decision_batch_id: "batch-1",
      disabled_reason: null,
    },
    automation: { level: "A1", reason: "user_decision_required", requires_user: true },
    progress: {
      phase: "planning",
      percent: null,
      completed_subjects: null,
      failed_subjects: null,
      excluded_subjects: null,
      total_subjects: null,
    },
    decision_batch: {
      batch_id: "batch-1",
      evidence_snapshot_hash: "evidence-1",
      plan_hash_before: null,
      expires_at: "2027-01-01T00:00:00Z",
      items: [
        {
          item_id: "atlas",
          kind: "atlas",
          question: "Choose an atlas",
          impact: "Changes the analysis.",
          options: [{ id: "aal", label: "AAL", description: "Atlas A", recommended: true }],
          recommended_option: "aal",
          answer_type: "option",
          min_value: null,
          max_value: null,
          required: true,
          evidence_refs: [],
        },
      ],
    },
    approval_summary: null,
    result_summary: null,
    recovery: null,
    evidence_links: [],
    technical_details: null,
    created_at: "2026-08-18T00:00:00Z",
    updated_at: "2026-08-18T00:00:00Z",
  };
}

function controller(currentTask: AgentTaskResponse | null): AgentTaskController {
  return {
    answer: vi.fn().mockResolvedValue(undefined),
    approve: vi.fn().mockResolvedValue(undefined),
    approveRecovery: vi.fn().mockResolvedValue(undefined),
    cancel: vi.fn().mockResolvedValue(undefined),
    create: vi.fn().mockResolvedValue(undefined),
    dismissTask: vi.fn(),
    error: "",
    errorDetails: {},
    errorCode: null,
    events: [],
    harnessActivity: null,
    loading: false,
    loadHarnessActivity: vi.fn().mockResolvedValue(undefined),
    mutating: false,
    refresh: vi.fn().mockResolvedValue(undefined),
    selectTask: vi.fn().mockResolvedValue(undefined),
    task: currentTask,
    tasks: currentTask ? [currentTask] : [],
  };
}

function Harness({
  activeProjectId,
  controller: activeController,
}: {
  activeProjectId: string | null;
  controller: AgentTaskController;
}) {
  const attention = useAgentAttentionDialog(activeController, activeProjectId);
  return (
    <>
      <button onClick={attention.reopen} type="button">
        Reopen attention
      </button>
      <AgentAttentionDialog attention={attention} controller={activeController} />
    </>
  );
}

describe("AgentAttentionDialog", () => {
  it("opens a decision batch automatically and submits exactly one existing answer command", () => {
    const activeController = controller(task());
    render(
      <I18nProvider locale="en">
        <Harness activeProjectId="project-1" controller={activeController} />
      </I18nProvider>,
    );

    const dialog = screen.getByRole("dialog", { name: "Confirm decisions" });
    expect(dialog).toHaveTextContent("The agent will continue planning automatically");
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm and continue" }));

    expect(activeController.answer).toHaveBeenCalledTimes(1);
    expect(activeController.answer).toHaveBeenCalledWith("batch-1", [
      { item_id: "atlas", value: "aal" },
    ]);
  });

  it("dismisses one action locally, reopens it without mutation, and clears it on project change", () => {
    const activeController = controller(task());
    const { rerender } = render(
      <I18nProvider locale="en">
        <Harness activeProjectId="project-1" controller={activeController} />
      </I18nProvider>,
    );

    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(activeController.answer).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Reopen attention" }));
    expect(screen.getByRole("dialog", { name: "Confirm decisions" })).toBeInTheDocument();

    rerender(
      <I18nProvider locale="en">
        <Harness activeProjectId="project-2" controller={controller(task("project-1"))} />
      </I18nProvider>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("uses the approval summary hash as a distinct action identity", () => {
    const approvalTask: AgentTaskResponse = {
      ...task(),
      next_action: {
        type: "approve_execution",
        title: "Approve execution",
        description: null,
        requires_user: true,
        decision_batch_id: null,
        disabled_reason: null,
      },
      decision_batch: null,
      approval_summary: {
        summary_hash: "summary-a",
        execution_environment_snapshot_id: "environment-a",
        execution_environment_hash: "environment-hash-a",
        goal: "Generate FC",
        dataset_summary: "One subject",
        execution_summary: "Native FC",
        write_roots: ["project://derivatives"],
        rawdata_read_only: true,
        external_tools: [],
        limitations: [],
        science_changes: [],
        sections: [],
        expires_at: null,
      },
    };
    const activeController = controller(approvalTask);
    render(
      <I18nProvider locale="en">
        <Harness activeProjectId="project-1" controller={activeController} />
      </I18nProvider>,
    );

    const dialog = screen.getByRole("dialog", { name: "Approve and start execution" });
    expect(dialog).toHaveTextContent("project://derivatives");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Approve and continue automatically" }),
    );
    expect(activeController.approve).toHaveBeenCalledTimes(1);
  });
});
