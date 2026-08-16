import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import { RunsWorkspace } from "../RunsWorkspace";
import type { TaskLogEntry } from "../../../lib/types/task";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import {
  getProjectRun,
  getProjectRunStateTimeline,
  listProjectRunArtifacts,
  listProjectRunEvents,
  listProjectRunLogs,
} from "../../../lib/api/projectRuns";
import { listSandboxAttempts } from "../../../lib/api/sandboxes";

vi.mock("../../../lib/api/projectRuns", () => ({
  getProjectRun: vi.fn(),
  getProjectRunStateTimeline: vi.fn(),
  listProjectRunArtifacts: vi.fn(),
  listProjectRunEvents: vi.fn(),
  listProjectRunLogs: vi.fn(),
}));

vi.mock("../../../lib/api/sandboxes", () => ({ listSandboxAttempts: vi.fn() }));

function agentTaskEvidence(): AgentTaskResponse {
  return {
    schema_version: 1,
    task_id: "lifecycle-1",
    project_id: "project-1",
    state: "running",
    outcome: null,
    goal_summary: "Generate FC and QC",
    current_action: "Running reviewed processing",
    next_action: {
      type: "none",
      title: "No action",
      description: null,
      requires_user: false,
      decision_batch_id: null,
      disabled_reason: null,
    },
    automation: {
      level: "A3",
      reason: "execution_or_validation_automatically",
      requires_user: false,
    },
    progress: {
      phase: "execution",
      percent: 42,
      completed_subjects: 1,
      failed_subjects: 0,
      excluded_subjects: 0,
      total_subjects: 3,
    },
    decision_batch: null,
    approval_summary: null,
    result_summary: null,
    recovery: null,
    evidence_links: [
      {
        id: "ticket",
        type: "execution_ticket",
        label: "Execution ticket",
        uri: "project://tickets/ticket-1",
        available: true,
      },
    ],
    technical_details: {
      lifecycle_id: "lifecycle-1",
      internal_state: "RUNNING",
      reviewed_plan_id: "plan-1",
      plan_hash: "sha256:plan",
      goal_contract_id: "goal-1",
      goal_hash: "sha256:goal",
      ticket_id: "ticket-1",
      run_id: "run-1",
      observation_id: null,
      evaluation_id: null,
      backend: { requested: "auto", selected: "cpu", fallback_reason: "GPU stage not allowlisted" },
      node_ids: ["native_preproc", "fc"],
    },
    created_at: "2026-07-16T00:00:00Z",
    updated_at: "2026-07-16T00:01:00Z",
  };
}

function planOnlyAgentTaskEvidence(): AgentTaskResponse {
  return {
    ...agentTaskEvidence(),
    state: "completed",
    outcome: "succeeded",
    progress: {
      phase: "complete",
      percent: null,
      completed_subjects: null,
      failed_subjects: null,
      excluded_subjects: null,
      total_subjects: null,
    },
    evidence_links: [
      {
        id: "reviewed-plan",
        type: "reviewed_plan",
        label: "Reviewed plan",
        uri: "project://plans/plan-1",
        available: true,
      },
    ],
    technical_details: {
      ...agentTaskEvidence().technical_details!,
      ticket_id: null,
      run_id: null,
      backend: null,
    },
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
      recommended_action: null,
      artifacts: [
        {
          artifact_id: "plan-1",
          artifact_type: "reviewed_plan",
          label: "Reviewed plan",
          uri: "project://plans/plan-1",
          checksum: "sha256:plan",
          capability_level: "metadata_only",
          reload_status: "passed",
        },
      ],
    },
  };
}

function task(overrides: Partial<TaskLogEntry> = {}): TaskLogEntry {
  return {
    id: "task-1",
    run_name: "Preprocessing run",
    pipeline: "rs-fMRI preprocessing",
    dataset: "Demo",
    status: "running",
    progress: 42,
    started_at: "2026-06-24T08:00:00Z",
    duration: "2m",
    owner: "local",
    logs: ["Detected inputs", "Running motion correction"],
    ...overrides,
  };
}

function mockProjectRunDetails({
  auditId = "audit-1",
  artifactPath = "outputs/run-1/report.json",
  eventMessage = "Running motion correction",
  logContent,
  nodeErrors = [],
  retryEligible = false,
}: {
  auditId?: string | null;
  artifactPath?: string;
  eventMessage?: string;
  logContent?: string;
  nodeErrors?: string[];
  retryEligible?: boolean;
} = {}) {
  vi.mocked(listSandboxAttempts).mockResolvedValue({
    ok: true,
    project_id: "project-1",
    run_id: "task-1",
    sandbox_attempts: [],
  });
  vi.mocked(getProjectRun).mockResolvedValue({
    ok: true,
    run_link: {
      run_link_id: "link-1",
      project_id: "project-1",
      reviewed_plan_id: "plan-1",
      run_id: "task-1",
      dispatch_id: "dispatch-1",
      task_id: null,
      pipeline_path: "work/pipeline.yaml",
      summary_path: artifactPath,
      project_config_path: "project.yaml",
      audit_id: auditId,
      status: nodeErrors.length ? "FAILED" : "RUNNING",
      created_at: "2026-06-24T08:00:00Z",
      updated_at: "2026-06-24T08:01:00Z",
      warnings: [] as string[],
      payload: {},
    },
    summary_preview: {
      run_id: "task-1",
      status: nodeErrors.length ? "failed" : "running",
      started_at: "2026-06-24T08:00:00Z",
      finished_at: nodeErrors.length ? "2026-06-24T08:02:00Z" : null,
    },
    warnings: [],
  });
  vi.mocked(listProjectRunEvents).mockResolvedValue({
    ok: true,
    project_id: "project-1",
    run_id: "task-1",
    events: [
      {
        timestamp: "2026-06-24T08:01:00Z",
        level: nodeErrors.length ? "error" : "info",
        source: "pipeline",
        message: eventMessage,
        node_id: "motion_qc",
        metadata: { progress: 42 },
      },
    ],
    warnings: [],
    errors: [],
  });
  vi.mocked(listProjectRunLogs).mockResolvedValue({
    ok: true,
    project_id: "project-1",
    run_id: "task-1",
    logs: [
      {
        log_id: "log-1",
        name: "pipeline.log",
        path: "logs/pipeline.log",
        relative_path: "logs/pipeline.log",
        exists: true,
        content: logContent ?? `Detected inputs\n${eventMessage}`,
        truncated: false,
        warnings: [],
      },
    ],
    warnings: [],
    errors: nodeErrors,
  });
  vi.mocked(listProjectRunArtifacts).mockResolvedValue({
    ok: true,
    project_id: "project-1",
    run_id: "task-1",
    artifacts: [
      {
        artifact_id: "artifact-1",
        name: "report",
        kind: "json",
        path: artifactPath,
        relative_path: artifactPath,
        exists: true,
        size_bytes: 20,
        modified_at: "2026-06-24T08:02:00Z",
        previewable: true,
        warnings: [],
      },
    ],
    warnings: [],
  });
  vi.mocked(getProjectRunStateTimeline).mockResolvedValue({
    ok: true,
    project_id: "project-1",
    run_id: "task-1",
    current_run_state: nodeErrors.length ? "failed" : "running",
    terminal: Boolean(nodeErrors.length),
    retry_eligible: retryEligible,
    resume_eligible: false,
    events: [
      {
        timestamp: "2026-06-24T08:01:00Z",
        state: nodeErrors.length ? "failed" : "running",
        source: "pipeline",
        message: eventMessage,
        node_id: "motion_qc",
        metadata: {},
      },
    ],
    nodes: [
      {
        node_id: "motion_qc",
        state: nodeErrors.length ? "failed" : "running",
        terminal: Boolean(nodeErrors.length),
        retry_eligible: retryEligible,
        reuse_eligible: false,
        warnings: [],
        errors: nodeErrors,
        metadata: {},
      },
    ],
    warnings: [],
    errors: [],
  });
}

function renderWorkspace(
  overrides: Partial<ComponentProps<typeof RunsWorkspace>> = {},
  locale: "en" | "zh-CN" = "en",
) {
  const selectedTask = task();
  const props: ComponentProps<typeof RunsWorkspace> = {
    baseUrl: "http://api",
    error: "",
    loading: false,
    onRetryTasks: vi.fn(),
    onSelectTask: vi.fn(),
    projectId: "project-1",
    selectedTask,
    selectedTaskId: selectedTask.id,
    tasks: [
      selectedTask,
      task({
        id: "task-2",
        run_name: "QC report",
        pipeline: "rs-fMRI QC",
        status: "failed",
        progress: 88,
        logs: ["FD summary missing"],
      }),
      task({
        id: "task-3",
        run_name: "Completed export",
        pipeline: "Report export",
        status: "completed",
        progress: 100,
      }),
    ],
    historyTasks: [],
  };
  props.historyTasks = props.tasks;

  render(
    <I18nProvider locale={locale}>
      <RunsWorkspace {...props} {...overrides} />
    </I18nProvider>,
  );
  return { props };
}

describe("RunsWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockProjectRunDetails();
  });

  it("shows run history summary, table, and selected run details", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    expect(screen.getByRole("heading", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tasks" })).toBeInTheDocument();
    expect(screen.getByLabelText("Selected run detail")).toHaveTextContent("Preprocessing run");
    expect(screen.getByLabelText("Run facts")).toHaveTextContent("rs-fMRI preprocessing");
    expect(screen.getByLabelText("Pipeline timeline")).toHaveTextContent(
      "Running motion correction",
    );
    expect(screen.getByLabelText("Selected node inspector")).toHaveTextContent(
      "rs-fMRI preprocessing",
    );

    await user.click(screen.getByRole("radio", { name: "Logs" }));

    expect(screen.getByLabelText("Run logs")).toHaveTextContent("Running motion correction");

    await user.click(screen.getByRole("radio", { name: "History" }));

    expect(screen.getByRole("heading", { name: "Execution runs" })).toBeInTheDocument();
    expect(screen.getByText(/selected project's run-history API/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Run history overview")).toHaveTextContent("3");
    expect(screen.getByRole("table", { name: "Project run history" })).toHaveTextContent(
      "Preprocessing run",
    );
    expect(screen.getByRole("table", { name: "Project run history" })).toHaveTextContent(
      "QC report",
    );
  });

  it("keeps canonical Agent Task ticket, backend, and evidence visible in Runs", async () => {
    const user = userEvent.setup();
    renderWorkspace({ agentTask: agentTaskEvidence() });
    await user.click(screen.getByRole("radio", { name: "History" }));

    const panel = screen.getByRole("region", { name: "Agent Task evidence" });
    expect(panel).toHaveTextContent("lifecycle-1");
    expect(panel).toHaveTextContent("ticket-1");
    expect(panel).toHaveTextContent("auto → cpu");
    expect(panel).toHaveTextContent("project://tickets/ticket-1");
  });

  it("keeps approval navigation backend-authoritative in the task workspace", async () => {
    const user = userEvent.setup();
    const onOpenAgent = vi.fn();
    const waitingTask: AgentTaskResponse = {
      ...agentTaskEvidence(),
      state: "waiting_for_user",
      current_action: "Review approval summary",
      next_action: {
        type: "approve_execution",
        title: "Approve execution",
        description: null,
        requires_user: true,
        decision_batch_id: null,
        disabled_reason: null,
      },
      approval_summary: {
        summary_hash: "sha256:summary",
        execution_environment_snapshot_id: "environment-1",
        execution_environment_hash: "environment-hash-1",
        goal: "Generate FC and QC",
        dataset_summary: "3 subjects",
        execution_summary: "Reviewed native preprocessing",
        write_roots: ["derivatives"],
        rawdata_read_only: true,
        external_tools: [],
        limitations: ["Research use only"],
        science_changes: [],
        sections: [],
        expires_at: null,
      },
    };

    renderWorkspace({
      agentTask: waitingTask,
      onOpenAgent,
      selectedTask: null,
      selectedTaskId: null,
    });

    const panel = screen.getByLabelText("Reviewed execution status");
    expect(panel).toHaveTextContent("Rawdata read-only");
    expect(panel).toHaveTextContent("Summary hash bound");
    expect(panel).toHaveTextContent("backend remain authoritative");

    await user.click(screen.getByRole("button", { name: "Review approval" }));
    expect(onOpenAgent).toHaveBeenCalledTimes(1);
  });

  it("does not project an unrelated current Agent Task onto the selected run", () => {
    renderWorkspace({ agentTask: agentTaskEvidence() });

    const projection = screen.getByLabelText("Selected run projection");
    expect(projection).toHaveTextContent("No associated Agent Task");
    expect(projection).not.toHaveTextContent("Running reviewed processing");
    expect(screen.queryByLabelText("Reviewed execution status")).not.toBeInTheDocument();
  });

  it("projects the associated Agent Task terminal result instead of its stale approval summary", () => {
    const terminalTask: AgentTaskResponse = {
      ...agentTaskEvidence(),
      state: "needs_attention",
      outcome: "partial",
      current_action: "Inspect the evidence and choose a safe next step.",
      technical_details: {
        ...agentTaskEvidence().technical_details!,
        run_id: "task-1",
      },
      approval_summary: {
        summary_hash: "sha256:summary",
        execution_environment_snapshot_id: "environment-1",
        execution_environment_hash: "environment-hash-1",
        goal: "Generate FC and QC",
        dataset_summary: "1 selected subject",
        execution_summary: "1 reviewed node; no dispatch before approval",
        write_roots: ["work"],
        rawdata_read_only: true,
        external_tools: [],
        limitations: ["Pre-approval limitation"],
        science_changes: [],
        sections: [],
        expires_at: null,
      },
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
        summary: "The selected subject completed with a scientific warning.",
        qc_summary: "Nuisance regression requires review.",
        completed_subjects: 1,
        failed_subjects: 0,
        excluded_subjects: 0,
        total_subjects: 1,
        limitations: ["Scientific warning remains"],
        recommended_action: "Review QC evidence",
        artifacts: [],
      },
    };

    renderWorkspace({
      agentTask: terminalTask,
      selectedTask: task({ id: "task-1", status: "partial", progress: 100 }),
      selectedTaskId: "task-1",
    });

    const projection = screen.getByLabelText("Selected run projection");
    expect(projection).toHaveTextContent("Research goal not fully satisfied");
    expect(projection).toHaveTextContent("1 / 1 subjects completed");
    expect(projection).not.toHaveTextContent("no dispatch before approval");
  });

  it("localizes stable terminal result messages in the selected-run projection", () => {
    const terminalTask: AgentTaskResponse = {
      ...agentTaskEvidence(),
      state: "needs_attention",
      outcome: "partial",
      technical_details: {
        ...agentTaskEvidence().technical_details!,
        run_id: "task-1",
      },
      result_summary: {
        outcome: "partial",
        title: "Research goal not fully satisfied",
        summary: "Some reviewed evidence failed or remained incomplete.",
        qc_summary: null,
        completed_subjects: 1,
        failed_subjects: 0,
        excluded_subjects: 0,
        total_subjects: 1,
        limitations: ["A scientifically simplified method was used; review its limitations."],
        recommended_action: null,
        artifacts: [],
      },
    };

    renderWorkspace(
      {
        agentTask: terminalTask,
        selectedTask: task({ id: "task-1", status: "partial", progress: 100 }),
        selectedTaskId: "task-1",
      },
      "zh-CN",
    );

    const projection = screen.getByLabelText("所选运行投影");
    expect(projection).toHaveTextContent("研究目标未完全满足");
    expect(projection).toHaveTextContent("部分已审核证据失败或仍不完整");
    expect(projection).toHaveTextContent("使用了科学上简化的方法；请审阅其限制");
    expect(projection).not.toHaveTextContent("Research goal not fully satisfied");
  });

  it("does not render Agent Task evidence owned by a different project", () => {
    renderWorkspace({
      agentTask: { ...agentTaskEvidence(), project_id: "project-2" },
    });

    expect(screen.queryByRole("region", { name: "Agent Task evidence" })).not.toBeInTheDocument();
  });

  it("renders plan-only evidence as not executed instead of unavailable backend state", async () => {
    const user = userEvent.setup();
    renderWorkspace({ agentTask: planOnlyAgentTaskEvidence() }, "zh-CN");
    await user.click(screen.getByRole("radio", { name: "历史" }));

    const panel = screen.getByRole("region", { name: "Agent Task 证据" });
    expect(panel).toHaveTextContent("已完成");
    expect(panel).toHaveTextContent("执行状态");
    expect(panel).toHaveTextContent("未执行（仅方案）");
    expect(panel).toHaveTextContent("执行票据");
    expect(panel).toHaveTextContent("未创建");
    expect(panel).toHaveTextContent("未涉及执行后端（仅方案任务）");
    expect(panel).toHaveTextContent("审核方案");
    expect(panel).not.toHaveTextContent("Reviewed plan");
  });

  it("selects a run from the list", async () => {
    const user = userEvent.setup();
    const onSelectTask = vi.fn();
    renderWorkspace({ onSelectTask });

    await user.click(screen.getByRole("button", { name: /QC report/ }));

    expect(onSelectTask).toHaveBeenCalledWith("task-2");
  });

  it("caps long run logs with an explicit rendering budget note", async () => {
    const user = userEvent.setup();
    const longLogs = Array.from({ length: 18 }, (_, index) => `Log line ${index + 1}`);

    mockProjectRunDetails({ logContent: longLogs.join("\n") });
    renderWorkspace({
      selectedTask: task({ logs: longLogs }),
    });

    await user.click(screen.getByRole("radio", { name: "Logs" }));

    expect(screen.getByRole("status")).toHaveTextContent("Showing latest 12 of 18 log lines");
    expect(screen.queryByText("Log line 1")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Run logs")).toHaveTextContent("Log line 18");
  });

  it("filters runs by status and search text", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getByRole("radio", { name: "History" }));

    await user.click(screen.getByRole("radio", { name: "Failed" }));

    const table = screen.getByRole("table", { name: "Project run history" });
    expect(table).toHaveTextContent("QC report");
    expect(table).not.toHaveTextContent("Preprocessing run");

    await user.type(screen.getByLabelText("Search runs"), "motion");

    expect(table).toHaveTextContent("No runs match the current search and status filters");
  });

  it("separates empty, loading, and error-without-rows states", async () => {
    const user = userEvent.setup();
    renderWorkspace({
      error: "Backend unavailable",
      loading: false,
      selectedTask: null,
      selectedTaskId: null,
      tasks: [],
      historyTasks: [],
    });
    await user.click(screen.getByRole("radio", { name: "History" }));

    const table = screen.getByRole("table", { name: "Project run history" });

    expect(table).toHaveTextContent("Run history unavailable");
    expect(screen.getByText(/Backend unavailable/)).toBeInTheDocument();
    expect(table).not.toHaveTextContent("No runs match the current search");
  });

  it("keeps stale rows visible when refresh fails with existing rows", async () => {
    const user = userEvent.setup();
    renderWorkspace({
      error: "Refresh failed",
      loading: false,
    });
    await user.click(screen.getByRole("radio", { name: "History" }));

    const table = screen.getByRole("table", { name: "Project run history" });

    expect(screen.getByText(/showing last loaded rows/i)).toBeInTheDocument();
    expect(table).toHaveTextContent("Preprocessing run");
    expect(screen.queryByLabelText("Run stream status")).not.toBeInTheDocument();
  });

  it("does not expose global task stream controls for project history records", () => {
    renderWorkspace({
      selectedTask: task({ status: "completed", progress: 100 }),
      tasks: [task({ status: "completed", progress: 100 })],
    });

    expect(screen.queryByText("Reconnect")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Run stream status")).not.toBeInTheDocument();
  });

  it("shows diagnostics, artifact, and audit detail sections", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    mockProjectRunDetails({
      auditId: "audit-run-1",
      artifactPath: "outputs/run-1/report.json",
      eventMessage: "Mean FD above threshold",
      nodeErrors: ["Motion QC failed"],
      retryEligible: true,
    });
    renderWorkspace({
      selectedTask: task({
        status: "failed",
        progress: 88,
        result_path: "outputs/run-1/report.json",
        logs: ["FD summary missing"],
      }),
    });

    await user.click(screen.getByRole("radio", { name: "Diagnostics" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Failed run");
    expect(screen.getByLabelText("Failed node actions")).toHaveTextContent("Failed node response");
    expect(screen.getByRole("button", { name: "Retry Allowed Step" })).toBeDisabled();
    expect(screen.getByLabelText("Failed node actions")).toHaveTextContent(
      "no project-scoped, reviewed retry command contract",
    );

    await user.click(screen.getByRole("button", { name: "Explain Error" }));

    expect(screen.getByLabelText("Failure explanation")).toHaveTextContent("motion_qc");

    await user.click(screen.getByRole("button", { name: "Copy Diagnostics" }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Motion QC failed"));
    expect(screen.getByLabelText("Failed node actions")).toHaveTextContent("Diagnostics copied");
    expect(screen.getByLabelText("Run diagnostics")).toHaveTextContent("Motion QC failed");
    expect(screen.getByLabelText("Pipeline timeline")).toHaveTextContent("Mean FD above threshold");

    await user.click(screen.getByRole("radio", { name: "Artifacts" }));

    expect(screen.getByLabelText("Run artifacts")).toHaveTextContent("outputs/run-1/report.json");

    await user.click(screen.getByRole("radio", { name: "Audit" }));

    expect(screen.getByLabelText("Run audit")).toHaveTextContent("audit-run-1");
    expect(screen.getByLabelText("Run audit")).toHaveTextContent("dispatch-1");
    expect(screen.queryByRole("button", { name: "Request Audit Package" })).not.toBeInTheDocument();
  });

  it("renders the complete selected-run artifact scope with registry metadata", async () => {
    vi.mocked(listProjectRunArtifacts).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      run_id: "task-1",
      artifacts: [
        {
          artifact_id: "artifact-filtered",
          registered_artifact_id: "registered-filtered",
          artifact_type: "filtered_bold",
          stage_id: "temporal_filtering",
          subject_id: "sub-001",
          registration_status: "registered",
          name: "sub-001_filtered_bold.nii.gz",
          kind: "nifti",
          path: "preprocessing_native_runs/task-1/sub-001_filtered_bold.nii.gz",
          relative_path: "preprocessing_native_runs/task-1/sub-001_filtered_bold.nii.gz",
          exists: true,
          size_bytes: 20,
          modified_at: "2026-06-24T08:02:00Z",
          previewable: false,
          warnings: [],
        },
        ...["qc_json", "validation_report", "final_report"].map((artifactType) => ({
          artifact_id: `artifact-${artifactType}`,
          registered_artifact_id: `registered-${artifactType}`,
          artifact_type: artifactType,
          stage_id: artifactType,
          subject_id: "sub-001",
          registration_status: "registered",
          name: `${artifactType}.json`,
          kind: "json",
          path: `work/pipeline_runs/task-1/${artifactType}.json`,
          relative_path: `work/pipeline_runs/task-1/${artifactType}.json`,
          exists: true,
          size_bytes: 20,
          modified_at: "2026-06-24T08:02:00Z",
          previewable: true,
          warnings: [] as string[],
        })),
      ],
      warnings: [] as string[],
    });

    renderWorkspace();

    expect(await screen.findByText("sub-001_filtered_bold.nii.gz")).toBeInTheDocument();
    expect(screen.getByLabelText("Artifacts")).toHaveTextContent("sub-001 · temporal_filtering");
    expect(screen.getByLabelText("Artifacts")).toHaveTextContent("validation_report");
    expect(screen.getByLabelText("Artifacts")).toHaveTextContent("final_report");
    expect(listProjectRunArtifacts).toHaveBeenCalledWith("http://api", "project-1", "task-1");
  });

  it("orders selected-run timeline events chronologically before rendering", async () => {
    vi.mocked(listProjectRunEvents).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      run_id: "task-1",
      events: [
        {
          timestamp: "2026-07-26T07:48:47.978398+00:00",
          level: "info",
          source: "pipeline",
          message: "Pipeline execution started.",
          metadata: { progress: 100 },
        },
        {
          timestamp: "2026-07-26T07:48:47Z",
          level: "info",
          source: "pipeline",
          message: "Run link created with status SUCCESS.",
          metadata: { progress: 0 },
        },
      ],
      warnings: [],
      errors: [],
    });
    vi.mocked(getProjectRunStateTimeline).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      run_id: "task-1",
      current_run_state: "succeeded",
      terminal: true,
      retry_eligible: false,
      resume_eligible: false,
      events: [
        {
          timestamp: "2026-07-26T07:48:47.978398+00:00",
          state: "running",
          source: "summary",
          message: "Pipeline execution started.",
          metadata: {},
        },
        {
          timestamp: "2026-07-26T07:48:47Z",
          state: "created",
          source: "run_link",
          message: "Run link created.",
          metadata: {},
        },
      ],
      nodes: [],
      warnings: [],
      errors: [],
    });

    renderWorkspace();

    const timeline = await screen.findByLabelText("Pipeline timeline");
    expect(timeline.textContent?.indexOf("Run link created.")).toBeLessThan(
      timeline.textContent?.indexOf("Pipeline execution started.") ?? -1,
    );
    const events = screen.getByLabelText("Run events");
    expect(events.textContent?.indexOf("Run link created with status SUCCESS.")).toBeLessThan(
      events.textContent?.indexOf("Pipeline execution started.") ?? -1,
    );
  });

  it("localizes stable backend run events in Chinese", async () => {
    vi.mocked(listProjectRunEvents).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      run_id: "task-1",
      events: [
        {
          timestamp: "2026-07-26T07:48:47Z",
          level: "info",
          source: "run_link",
          message: "Run link created with status SUCCESS.",
          metadata: {},
        },
        {
          timestamp: "2026-07-26T07:48:49Z",
          level: "info",
          source: "summary",
          message: "Pipeline finished with status SUCCESS.",
          metadata: {},
        },
      ],
      warnings: [],
      errors: [],
    });
    vi.mocked(getProjectRunStateTimeline).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      run_id: "task-1",
      current_run_state: "succeeded",
      terminal: true,
      retry_eligible: false,
      resume_eligible: false,
      events: [
        {
          timestamp: "2026-07-26T07:48:47Z",
          state: "created",
          source: "run_link",
          message: "Run link created.",
          metadata: {},
        },
        {
          timestamp: "2026-07-26T07:48:49Z",
          state: "succeeded",
          source: "summary",
          message: "Pipeline finished: succeeded.",
          metadata: {},
        },
      ],
      nodes: [],
      warnings: [],
      errors: [],
    });

    renderWorkspace({}, "zh-CN");

    expect(await screen.findByLabelText("流程时间线")).toHaveTextContent("运行关联已创建");
    expect(screen.getByLabelText("流程时间线")).toHaveTextContent("流水线已完成：成功");
    expect(screen.getByLabelText("运行事件")).toHaveTextContent("运行关联已创建，状态为成功");
    expect(screen.getByLabelText("运行事件")).not.toHaveTextContent("Run link created");
  });

  it("uses an explicit empty state when no project is selected", () => {
    renderWorkspace({
      projectId: null,
      selectedTask: null,
      selectedTaskId: null,
      tasks: [],
    });

    expect(screen.getByText("Select a project before reviewing runs")).toBeInTheDocument();
    expect(screen.queryByText("Run list unavailable")).not.toBeInTheDocument();
    expect(screen.queryByText("Select a run to inspect")).not.toBeInTheDocument();
  });

  it("rejects cross-project detail responses instead of rendering their evidence", async () => {
    vi.mocked(listProjectRunEvents).mockResolvedValue({
      ok: true,
      project_id: "project-2",
      run_id: "task-1",
      events: [
        {
          level: "info",
          source: "pipeline",
          message: "Cross-project event",
          metadata: {},
        },
      ],
      warnings: [],
      errors: [],
    });

    renderWorkspace();

    expect(
      await screen.findByText("Project run response did not match the selected project and run."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Cross-project event")).not.toBeInTheDocument();
  });

  it("renders run history and details in Chinese", async () => {
    const user = userEvent.setup();
    renderWorkspace({}, "zh-CN");
    await user.click(screen.getByRole("radio", { name: "历史" }));

    expect(screen.getByRole("heading", { name: "运行记录" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "执行运行" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "项目运行历史" })).toBeInTheDocument();
    expect(screen.getByLabelText("运行历史概览")).toHaveTextContent("已加载项目运行记录");

    await user.click(screen.getByRole("radio", { name: "日志" }));

    expect(screen.getByLabelText("运行日志")).toHaveTextContent("Running motion correction");
  });
});
