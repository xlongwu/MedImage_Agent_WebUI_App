import { render, screen, within } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import { AgentWorkspaceView } from "../AgentWorkspace";
import type { AgentTaskController } from "../useAgentTaskController";

function approvalTask(): AgentTaskResponse {
  return {
    schema_version: 1,
    task_id: "task-1",
    project_id: "project-1",
    state: "waiting_for_user",
    outcome: null,
    goal_summary: "Preprocess three subjects and generate FC",
    current_action: "The reviewed plan is ready for approval.",
    current_action_code: "waiting_approval",
    next_action: {
      type: "approve_execution",
      title: "Approve the processing plan",
      description: "Review the bounded write scope before execution.",
      requires_user: true,
      decision_batch_id: null,
      disabled_reason: null,
    },
    automation: {
      level: "A1",
      reason: "user_decision_required",
      requires_user: true,
    },
    progress: {
      phase: "plan_ready",
      percent: 25,
      completed_subjects: 0,
      failed_subjects: 0,
      excluded_subjects: 0,
      total_subjects: 3,
    },
    decision_batch: null,
    approval_summary: {
      summary_hash: "sha256:summary",
      execution_environment_snapshot_id: "environment-1",
      execution_environment_hash: "environment-hash-1",
      goal: "Preprocess three subjects and generate FC",
      dataset_summary: "3 subjects · converted BIDS",
      execution_summary: "Reviewed native preprocessing and FC",
      write_roots: ["project://derivatives", "project://runs"],
      rawdata_read_only: true,
      external_tools: [],
      limitations: ["GPU auto may select CPU"],
      science_changes: ["Schaefer 200 atlas"],
      sections: [],
      expires_at: null,
    },
    result_summary: null,
    recovery: null,
    evidence_links: [
      {
        id: "plan",
        type: "reviewed_plan",
        label: "Reviewed plan",
        uri: "project://plan/1",
        available: true,
      },
    ],
    technical_details: {
      lifecycle_id: "task-1",
      internal_state: "WAITING_FOR_APPROVAL",
      reviewed_plan_id: "plan-1",
      plan_hash: "sha256:plan",
      goal_contract_id: "goal-1",
      goal_hash: "sha256:goal",
      ticket_id: null,
      run_id: null,
      observation_id: null,
      evaluation_id: null,
      backend: { requested: "auto", selected: "cpu", fallback_reason: "GPU stage not allowlisted" },
      node_ids: ["native_preproc", "fc"],
    },
    created_at: "2026-07-16T00:00:00Z",
    updated_at: "2026-07-16T00:00:00Z",
  };
}

function controller(task: AgentTaskResponse | null): AgentTaskController {
  return {
    answer: vi.fn(),
    approve: vi.fn(),
    approveRecovery: vi.fn(),
    cancel: vi.fn(),
    create: vi.fn(),
    dismissTask: vi.fn(),
    error: "",
    harnessActivity: null,
    events: [],
    loading: false,
    loadHarnessActivity: vi.fn(),
    mutating: false,
    refresh: vi.fn(),
    selectTask: vi.fn(),
    task,
    tasks: task ? [task] : [],
  };
}

describe("AgentWorkspace", () => {
  it("renders a reviewed-execution safety block as actionable policy guidance", () => {
    render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{
            ...controller(approvalTask()),
            error: "AGENT_EXECUTION_BLOCKED: REVIEWED_EXECUTION_DISABLED",
          }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("受审执行尚未启用");
    expect(alert).toHaveTextContent("MEDIMAGE_ENABLE_REVIEWED_EXECUTION=1");
    expect(alert).not.toHaveTextContent("Agent Task 服务不可用");
    expect(alert).not.toHaveTextContent('{"ok":false');
  });

  it("renders an expired approval summary as a recoverable task state without raw JSON", () => {
    render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{
            ...controller(approvalTask()),
            error: "APPROVAL_SUMMARY_EXPIRED",
          }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("审批摘要已过期");
    expect(alert).toHaveTextContent("请取消该任务后重新创建方案");
    expect(alert).toHaveTextContent("未启动任何计算");
    expect(alert).not.toHaveTextContent("Agent Task 服务不可用");
    expect(alert).not.toHaveTextContent("APPROVAL_SUMMARY_EXPIRED");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders a missing ReHo preprocessing chain as actionable guidance", () => {
    render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{
            ...controller(approvalTask()),
            error:
              "AGENT_EXECUTION_PREREQUISITE_MISSING: ReHo execution requires a realignment or smoothing producer.",
          }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("执行前置条件未满足");
    expect(alert).toHaveTextContent("缺少 ReHo 所需的前处理输入链");
    expect(alert).toHaveTextContent("未创建执行票据或运行");
    expect(alert).toHaveTextContent("请取消该任务后重新创建方案");
    expect(alert).not.toHaveTextContent("Agent Task 服务不可用");
    expect(alert).not.toHaveTextContent("AGENT_EXECUTION_PREREQUISITE_MISSING");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders a blocked dry-run without exposing the backend status code", () => {
    render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{
            ...controller(approvalTask()),
            error: "AGENT_DRY_RUN_BLOCKED: PLAN_ADAPTER_FAILED",
          }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("执行前检查未通过");
    expect(alert).toHaveTextContent("未创建执行票据或运行");
    expect(alert).toHaveTextContent("请取消该任务后重新创建方案");
    expect(alert).not.toHaveTextContent("Agent Task 服务不可用");
    expect(alert).not.toHaveTextContent("AGENT_DRY_RUN_BLOCKED");
    expect(alert).not.toHaveTextContent("PLAN_ADAPTER_FAILED");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });

  it("does not misclassify another structured execution block as a connection failure", () => {
    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{
            ...controller(approvalTask()),
            error: "AGENT_EXECUTION_BLOCKED: SAFE_EXECUTION_POLICY_BLOCKED",
          }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The task action was not completed");
    expect(alert).toHaveTextContent("SAFE_EXECUTION_POLICY_BLOCKED");
    expect(alert).not.toHaveTextContent("Agent Task service is unavailable");
  });

  it.each([
    [
      "AGENT_EXECUTION_BLOCKED: EXECUTION_TICKET_EXPIRED",
      "Execution ticket expired",
      "expired ticket cannot be reused",
    ],
    [
      "AGENT_EXECUTION_BLOCKED: GATEWAY_DISPATCH_OUTCOME_UNKNOWN",
      "Dispatch outcome needs inspection",
      "executor will not be called again automatically",
    ],
    [
      "AGENT_EXECUTION_BLOCKED: EXECUTION_DISPATCH_FAILED",
      "Reviewed execution failed",
      "persisted dispatch evidence",
    ],
    [
      "MEMORY_STORE_UNHEALTHY",
      "Project memory is unavailable",
      "without substituting an empty memory context",
    ],
  ])("renders structured failure guidance for %s", (error, title, message) => {
    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{ ...controller(approvalTask()), error }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(title);
    expect(alert).toHaveTextContent(message);
    expect(alert).not.toHaveTextContent(error);
  });

  it("localizes stable approval counts in the Chinese workspace", () => {
    const task: AgentTaskResponse = {
      ...approvalTask(),
      approval_summary: {
        ...approvalTask().approval_summary!,
        dataset_summary: "2 registered subject(s)",
        execution_summary: "4 reviewed node(s); no dispatch before approval",
      },
    };

    render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByText("2 名已登记受试者")).toBeInTheDocument();
    expect(screen.getByText("4 个审核节点；批准前不会调度执行")).toBeInTheDocument();
    expect(screen.queryByText(/registered subject/)).not.toBeInTheDocument();
    expect(screen.queryByText(/reviewed node/)).not.toBeInTheDocument();
  });

  it("shows one primary action and keeps technical evidence behind Advanced Mode", () => {
    const task = approvalTask();
    const { rerender } = render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "Agent workspace" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for approval of the reviewed plan.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Approve plan" })).toHaveLength(1);
    expect(document.querySelectorAll('[data-primary-action="true"]')).toHaveLength(1);
    expect(screen.queryByText("sha256:plan")).not.toBeInTheDocument();

    rerender(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={true}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByText("sha256:plan")).toBeInTheDocument();
    expect(screen.getByText("GPU stage not allowlisted")).toBeInTheDocument();
  });

  it("renders an empty goal composer without manual dry-run controls", () => {
    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(null)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    const composer = screen.getByRole("region", { name: "Describe your research goal" });
    expect(within(composer).getByRole("textbox")).toBeInTheDocument();
    expect(within(composer).getByRole("button", { name: "Start task" })).toHaveAttribute(
      "data-primary-action",
      "true",
    );
    expect(
      screen.queryByRole("button", { name: /dry run|validation|report|refresh/i }),
    ).not.toBeInTheDocument();
  });

  it("shows one bounded recovery approval without exposing competing actions", () => {
    const task: AgentTaskResponse = {
      ...approvalTask(),
      state: "waiting_for_user",
      current_action: "One subject failed and a bounded recovery is ready.",
      next_action: {
        type: "approve_recovery",
        title: "Retry only the failed subject",
        description: "Successful subjects will not be rerun.",
        requires_user: true,
        decision_batch_id: null,
        disabled_reason: null,
      },
      recovery: {
        proposal_id: "recovery-1",
        diagnosis: "A temporary derivatives write failed for sub-003.",
        affected_subjects: ["sub-003"],
        recommended_action: "Retry the failed subject only",
        untouched_scope: ["sub-001", "sub-002", "rawdata"],
        requires_new_plan: false,
        approval_summary_hash: "sha256:recovery",
      },
      approval_summary: {
        ...approvalTask().approval_summary!,
        summary_hash: "sha256:recovery",
      },
    };

    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "Recovery proposal" })).toBeInTheDocument();
    expect(screen.getByText("sub-003")).toBeInTheDocument();
    expect(screen.getByText(/sub-001 · sub-002 · rawdata/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve recovery" })).toBeInTheDocument();
    expect(document.querySelectorAll('[data-primary-action="true"]')).toHaveLength(1);
  });

  it("shows partial results as limited evidence instead of success", () => {
    const task: AgentTaskResponse = {
      ...approvalTask(),
      state: "needs_attention",
      outcome: "partial",
      next_action: {
        type: "view_attention",
        title: "Review failed subject",
        description: null,
        requires_user: true,
        decision_batch_id: null,
        disabled_reason: null,
      },
      result_summary: {
        outcome: "partial",
        title: "Two of three subjects completed",
        summary: "FC was generated for two subjects; sub-003 is excluded.",
        qc_summary: "Two subjects passed QC",
        completed_subjects: 2,
        failed_subjects: 1,
        excluded_subjects: 1,
        total_subjects: 3,
        limitations: ["Group interpretation is incomplete"],
        recommended_action: "Review the recovery proposal",
        artifacts: [],
      },
      result_explanation: {
        outcome: "partial",
        completed_subjects: 2,
        failed_subjects: 1,
        excluded_subjects: 1,
        total_subjects: 3,
        artifact_refs: [],
        criteria: [],
        limitations: ["Group interpretation is incomplete"],
        recommended_action: "Review the recovery proposal",
        generated_text:
          "One subject needs recovery review before the cohort result can be interpreted.",
        generated_text_status: "accepted",
      },
    };

    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByText("Partially completed")).toBeInTheDocument();
    expect(screen.getByText("Group interpretation is incomplete")).toBeInTheDocument();
    expect(screen.getByText("Evidence-based explanation")).toBeInTheDocument();
    expect(
      screen.getByText(
        "One subject needs recovery review before the cohort result can be interpreted.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Completed$/)).not.toBeInTheDocument();
  });

  it("localizes backend-owned terminal result text and scientific limitations in Chinese", () => {
    const task: AgentTaskResponse = {
      ...approvalTask(),
      state: "needs_attention",
      outcome: "partial",
      progress: {
        phase: "complete",
        percent: 100,
        completed_subjects: 1,
        failed_subjects: 0,
        excluded_subjects: 0,
        total_subjects: 1,
      },
      result_summary: {
        outcome: "partial",
        title: "Research goal not fully satisfied",
        summary: "Some reviewed evidence failed or remained incomplete.",
        qc_summary: "1 validation check(s) passed; 0 failed.",
        completed_subjects: 1,
        failed_subjects: 0,
        excluded_subjects: 0,
        total_subjects: 1,
        limitations: ["A scientifically simplified method was used; review its limitations."],
        recommended_action: "Review technical evidence and the bounded recovery proposal.",
        artifacts: [],
      },
    };

    render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "研究目标未完全满足" })).toBeInTheDocument();
    expect(screen.getByText("部分已审核证据失败或仍不完整。")).toBeInTheDocument();
    expect(screen.getByText("使用了科学上简化的方法；请审阅其限制。")).toBeInTheDocument();
    expect(screen.queryByText("Research goal not fully satisfied")).not.toBeInTheDocument();
    expect(screen.getByText("执行处理").closest("li")).toHaveAttribute("data-state", "done");
    expect(screen.getByText("验证结果").closest("li")).toHaveAttribute("data-state", "done");
    expect(screen.getByText("完成").closest("li")).toHaveAttribute("data-state", "current");
  });

  it("shows plan-only completion without fake execution stages or a waiting next action", () => {
    const task: AgentTaskResponse = {
      ...approvalTask(),
      state: "completed",
      outcome: "succeeded",
      current_action: "The research goal has defensible result evidence.",
      next_action: {
        type: "review_results",
        title: "Review results",
        description: null,
        requires_user: false,
        decision_batch_id: null,
        disabled_reason: null,
      },
      progress: {
        phase: "complete",
        percent: null,
        completed_subjects: null,
        failed_subjects: null,
        excluded_subjects: null,
        total_subjects: null,
      },
      approval_summary: null,
      result_summary: {
        outcome: "succeeded",
        title: "Preprocessing plan prepared",
        summary: "A metadata-only plan was saved.",
        qc_summary: null,
        completed_subjects: null,
        failed_subjects: null,
        excluded_subjects: null,
        total_subjects: null,
        limitations: ["Metadata only"],
        recommended_action: "Review the plan",
        artifacts: [
          {
            artifact_id: "plan-1",
            artifact_type: "reviewed_plan",
            label: "Reviewed plan",
            uri: "project://project-1/reviewed_plan/plan-1",
            checksum: "sha256:plan",
            capability_level: "metadata_only",
            reload_status: "passed",
          },
        ],
      },
    };

    render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByText("执行处理").closest("li")).toHaveAttribute("data-state", "skipped");
    expect(screen.getByText("验证结果").closest("li")).toHaveAttribute("data-state", "skipped");
    expect(screen.getByText("0 项计算")).toBeInTheDocument();
    expect(screen.getByText("1 份审核方案")).toBeInTheDocument();
    expect(screen.getAllByText("预处理方案已准备完成")).toHaveLength(2);
    expect(screen.getByText("待处理项").closest("div")).toHaveTextContent("待处理项0");
    expect(screen.queryByRole("heading", { name: "查看结果" })).not.toBeInTheDocument();
    expect(screen.queryByText("等待你处理")).not.toBeInTheDocument();
  });

  it("renders goal revision and canceled terminal states in Chinese without stale actions", () => {
    const revisionTask: AgentTaskResponse = {
      ...approvalTask(),
      state: "waiting_for_user",
      current_action: "Waiting for one reviewed decision.",
      next_action: {
        type: "revise_goal",
        title: "Revise the research goal",
        description: "The goal did not match a supported workflow.",
        requires_user: true,
        decision_batch_id: "batch-revise",
        disabled_reason: null,
      },
      decision_batch: {
        batch_id: "batch-revise",
        evidence_snapshot_hash: "evidence-revise",
        plan_hash_before: null,
        expires_at: "2027-07-16T00:00:00Z",
        items: [
          {
            item_id: "goal_revision",
            kind: "goal_revision",
            question: "Revise the research goal.",
            impact: "UNSUPPORTED_GOAL",
            options: [],
            recommended_option: null,
            answer_type: "text",
            min_value: null,
            max_value: null,
            required: true,
            evidence_refs: [],
          },
        ],
      },
      approval_summary: null,
    };
    const { rerender } = render(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(revisionTask)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "审查所需决策" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认决策" })).toBeInTheDocument();
    expect(screen.queryByText("Waiting for one reviewed decision.")).not.toBeInTheDocument();

    const canceledTask: AgentTaskResponse = {
      ...revisionTask,
      state: "completed",
      outcome: "canceled",
      current_action: "The task needs attention before it can continue.",
      next_action: {
        type: "none",
        title: "Task canceled",
        description: null,
        requires_user: false,
        decision_batch_id: null,
        disabled_reason: null,
      },
      decision_batch: null,
    };
    rerender(
      <I18nProvider locale="zh-CN">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(canceledTask)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "任务已取消" })).toBeInTheDocument();
    expect(screen.getByText("执行处理").closest("li")).toHaveAttribute("data-state", "skipped");
    expect(screen.getByText("验证结果").closest("li")).toHaveAttribute("data-state", "skipped");
    expect(screen.getAllByText("任务在执行前被取消，已跳过")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "取消任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("reopens the global decision request without submitting a command", () => {
    const onReopenAttention = vi.fn();
    const task: AgentTaskResponse = {
      ...approvalTask(),
      next_action: {
        type: "answer_science_decision",
        title: "Answer decisions",
        description: null,
        requires_user: true,
        decision_batch_id: "batch-choices",
        disabled_reason: null,
      },
      decision_batch: {
        batch_id: "batch-choices",
        evidence_snapshot_hash: "evidence-choices",
        plan_hash_before: null,
        expires_at: "2027-07-16T00:00:00Z",
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
          {
            item_id: "gsr",
            kind: "global_signal_regression",
            question: "Include GSR",
            impact: "Changes correlations.",
            options: [],
            recommended_option: null,
            answer_type: "boolean",
            min_value: null,
            max_value: null,
            required: true,
            evidence_refs: [],
          },
          {
            item_id: "tr",
            kind: "repetition_time",
            question: "TR seconds",
            impact: "Controls filtering.",
            options: [],
            recommended_option: null,
            answer_type: "number",
            min_value: 0.1,
            max_value: 10,
            required: true,
            evidence_refs: [],
          },
          {
            item_id: "other",
            kind: "other",
            question: "Explain the scope",
            impact: "Documents the request.",
            options: [],
            recommended_option: null,
            answer_type: "text",
            min_value: null,
            max_value: null,
            required: true,
            evidence_refs: [],
          },
        ],
      },
    };

    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={controller(task)}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          onReopenAttention={onReopenAttention}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Confirm decisions" }));

    expect(onReopenAttention).toHaveBeenCalledTimes(1);
  });

  it("reopens the global execution approval without dispatching from the card", () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    const onReopenAttention = vi.fn();
    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{ ...controller(approvalTask()), approve: onApprove }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          onReopenAttention={onReopenAttention}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Approve plan" }));

    expect(onApprove).not.toHaveBeenCalled();
    expect(onReopenAttention).toHaveBeenCalledTimes(1);
  });

  it("reopens the global recovery approval without accepting it from the card", () => {
    const onApproveRecovery = vi.fn().mockResolvedValue(undefined);
    const onReopenAttention = vi.fn();
    const task: AgentTaskResponse = {
      ...approvalTask(),
      next_action: {
        type: "approve_recovery",
        title: "Approve recovery",
        description: "A bounded recovery is ready.",
        requires_user: true,
        decision_batch_id: null,
        disabled_reason: null,
      },
      recovery: {
        proposal_id: "recovery-1",
        diagnosis: "One subject needs a retry.",
        affected_subjects: ["sub-03"],
        recommended_action: "Rerun the failed subject only.",
        untouched_scope: ["sub-01", "sub-02"],
        requires_new_plan: false,
        approval_summary_hash: "sha256:recovery",
      },
    };
    render(
      <I18nProvider locale="en">
        <AgentWorkspaceView
          advancedMode={false}
          controller={{ ...controller(task), approveRecovery: onApproveRecovery }}
          dataStateLabel="Converted BIDS/NIfTI"
          onOpenRuns={vi.fn()}
          onReopenAttention={onReopenAttention}
          projectName="Demo Project"
        />
      </I18nProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Approve recovery" }));

    expect(onApproveRecovery).not.toHaveBeenCalled();
    expect(onReopenAttention).toHaveBeenCalledTimes(1);
  });
});
