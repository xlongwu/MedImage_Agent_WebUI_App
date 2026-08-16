import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectDetail } from "../../../lib/types/project";
import { I18nProvider } from "../../../i18n/I18nProvider";
import type { PresetPlanDraft, ReviewedPlanRecord } from "../../../types";
import { PlanWorkspace } from "../PlanWorkspace";

const pipelineApi = vi.hoisted(() => ({
  getProjectReviewedPlan: vi.fn(),
}));

vi.mock("../../../lib/api/pipeline", () => pipelineApi);

function project(overrides: Partial<ProjectDetail> = {}): ProjectDetail {
  return {
    id: "project-1",
    name: "Demo Project",
    study_id: "study-1",
    modality: "rs-fMRI",
    created_date: "2026-06-24",
    subjects_count: 4,
    current_pipeline_id: "not-selected",
    sequences: ["bold"],
    scans_count: 24,
    total_size: "128 MB",
    current_model_id: "model-1",
    metadata: {
      project_config_path: "work/projects/demo/project_config.yaml",
      dataset_index_path: "work/projects/demo/dataset_index.json",
      rawdata_dir: "work/projects/demo/rawdata",
      project_dir: "work/projects/demo",
    },
    ...overrides,
  };
}

function draft(overrides: Partial<PresetPlanDraft> = {}): PresetPlanDraft {
  return {
    preset_id: "preset-rsfmri",
    project_id: "project-1",
    goal: "Create a reviewed rs-fMRI preprocessing plan",
    source: "pipeline_preset",
    plan: {
      pipeline_id: "rsfmri_preprocessing",
      nodes: [
        {
          id: "load_bids",
          name: "Load BIDS inputs",
          description: "Read registered BIDS/NIfTI inputs.",
          backend: "bids_loader",
          inputs: ["dataset_index.json"],
          outputs: ["validated_input_manifest.json"],
          params: { strict_bids: true },
        },
        {
          id: "spm_realign",
          name: "Motion correction",
          description: "Prepare realignment through reviewed backend gates.",
          depends_on: ["load_bids"],
          backend: "spm",
          inputs: ["validated_input_manifest.json"],
          outputs: ["realignment_dry_run.json"],
          params: { quality: 0.9, dry_run_only: true },
        },
      ],
    },
    validation: {
      ok: true,
      errors: [],
      warnings: [],
      approval_required_nodes: ["spm_realign"],
      high_risk_nodes: ["spm_realign"],
      unknown_nodes: [],
    },
    next_actions: ["Review approval-required nodes", "Run dry-run before execution"],
    warnings: [],
    ...overrides,
  };
}

function renderWorkspace(overrides: Partial<ComponentProps<typeof PlanWorkspace>> = {}) {
  const onOpenDataConversion = vi.fn();
  const onOpenEnvironment = vi.fn();
  const onSelectedNodeChange = vi.fn();
  const selectedProject = project();

  render(
    <PlanWorkspace
      baseUrl="http://localhost"
      projectId="project-1"
      selectedProject={selectedProject}
      projectConfigPath={selectedProject.metadata?.project_config_path}
      datasetIndexPath={selectedProject.metadata?.dataset_index_path}
      rawdataDir={selectedProject.metadata?.rawdata_dir}
      projectDir={selectedProject.metadata?.project_dir}
      initialPresetDraft={null}
      onSelectedNodeChange={onSelectedNodeChange}
      onOpenDataConversion={onOpenDataConversion}
      onOpenEnvironment={onOpenEnvironment}
      {...overrides}
    />,
  );

  return { onOpenDataConversion, onOpenEnvironment, onSelectedNodeChange };
}

describe("PlanWorkspace", () => {
  beforeEach(() => {
    pipelineApi.getProjectReviewedPlan.mockReset();
  });

  it("requires a project before planning", () => {
    const { onOpenDataConversion } = renderWorkspace({
      projectId: null,
      selectedProject: null,
      projectConfigPath: undefined,
    });

    expect(screen.getByText("Select a project before planning")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Data & Conversion" }));
    expect(onOpenDataConversion).toHaveBeenCalledTimes(1);
  });

  it("routes missing project config to environment settings", () => {
    const { onOpenEnvironment } = renderWorkspace({
      selectedProject: project({ metadata: {} }),
      projectConfigPath: undefined,
    });

    expect(screen.getByText("Project config required")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Settings / Environment" }));
    expect(onOpenEnvironment).toHaveBeenCalledTimes(1);
  });

  it("shows a plan outline and node steps without opening technical tools by default", () => {
    renderWorkspace({ initialPresetDraft: draft() });

    expect(screen.getByRole("heading", { name: "Plan outline" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pipeline graph" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Plan pipeline steps" });
    expect(within(steps).getByText("Load BIDS inputs")).toBeInTheDocument();
    expect(within(steps).getByText("Motion correction")).toBeInTheDocument();
    expect(within(steps).getByText("High risk")).toBeInTheDocument();
    expect(screen.getByLabelText("Plan inspector")).toHaveTextContent("dataset_index.json");
    expect(screen.getByLabelText("Plan state machine")).toHaveTextContent("Needs Review");
    expect(screen.getByLabelText("Plan state machine")).toHaveTextContent("Dry-run Passed");
    expect(screen.queryByTestId("plan-review-console")).not.toBeInTheDocument();
  });

  it("renders the plan review surface in simplified Chinese", () => {
    const selectedProject = project();
    render(
      <I18nProvider locale="zh-CN">
        <PlanWorkspace
          baseUrl="http://localhost"
          projectId="project-1"
          selectedProject={selectedProject}
          projectConfigPath={selectedProject.metadata?.project_config_path}
          datasetIndexPath={selectedProject.metadata?.dataset_index_path}
          rawdataDir={selectedProject.metadata?.rawdata_dir}
          projectDir={selectedProject.metadata?.project_dir}
          initialPresetDraft={draft()}
          onOpenDataConversion={vi.fn()}
          onOpenEnvironment={vi.fn()}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "方案概要" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "流程图" })).toBeInTheDocument();
    expect(screen.getByLabelText("方案状态机")).toHaveTextContent("试运行已通过");
    expect(screen.getByRole("button", { name: "打开技术方案工具" })).toBeInTheDocument();
  });

  it("does not mark approval, dry-run, or execution readiness as reached without backend evidence", () => {
    renderWorkspace({ initialPresetDraft: draft() });

    const stateMachine = screen.getByLabelText("Plan state machine");

    expect(within(stateMachine).getByText("Approved").closest("li")).toHaveAttribute(
      "data-state",
      "pending-evidence",
    );
    expect(within(stateMachine).getByText("Dry-run Passed").closest("li")).toHaveAttribute(
      "data-state",
      "locked",
    );
    expect(within(stateMachine).getByText("Ready to Execute").closest("li")).toHaveAttribute(
      "data-state",
      "locked",
    );
    expect(screen.getByLabelText("Plan review facts")).toHaveTextContent("Approval evidence");
    expect(screen.getByLabelText("Plan review facts")).toHaveTextContent("Backend required");
  });

  it("shows later plan gates only when backend evidence is present", () => {
    renderWorkspace({
      initialPresetDraft: draft({
        validation: {
          ok: true,
          errors: [],
          warnings: [],
          approval_required_nodes: [],
          high_risk_nodes: [],
          unknown_nodes: [],
          approval_passed: true,
          dry_run_passed: true,
          ready_to_execute: true,
        },
      }),
    });

    const stateMachine = screen.getByLabelText("Plan state machine");

    expect(within(stateMachine).getByText("Approved").closest("li")).toHaveAttribute(
      "data-state",
      "completed",
    );
    expect(within(stateMachine).getByText("Dry-run Passed").closest("li")).toHaveAttribute(
      "data-state",
      "completed",
    );
    expect(within(stateMachine).getByText("Ready to Execute").closest("li")).toHaveAttribute(
      "data-state",
      "completed",
    );
    expect(within(stateMachine).getByText("Executed").closest("li")).toHaveAttribute(
      "data-state",
      "pending-evidence",
    );
  });

  it("updates the inspector and shared selection context when a pipeline node is selected", async () => {
    const { onSelectedNodeChange } = renderWorkspace({ initialPresetDraft: draft() });

    fireEvent.click(screen.getByRole("button", { name: "Inspect Motion correction" }));

    const inspector = screen.getByLabelText("Plan inspector");
    expect(inspector).toHaveTextContent("Motion correction");
    expect(inspector).toHaveTextContent("validated_input_manifest.json");
    expect(inspector).toHaveTextContent("realignment_dry_run.json");
    expect(inspector).toHaveTextContent("dry_run_only");
    expect(inspector).toHaveTextContent("High-risk or approval-sensitive node");
    await waitFor(() =>
      expect(onSelectedNodeChange).toHaveBeenLastCalledWith(
        expect.objectContaining({
          backend: "spm",
          id: "spm_realign",
          name: "Motion correction",
          risk: "High risk",
        }),
      ),
    );
  });

  it("opens technical plan tools without duplicating run history", () => {
    renderWorkspace({ initialPresetDraft: draft() });

    fireEvent.click(screen.getByRole("button", { name: "Open technical plan tools" }));

    expect(screen.getByText("No draft goal loaded yet")).toBeInTheDocument();
    expect(screen.queryByTestId("project-runs-panel")).not.toBeInTheDocument();
  });

  it("loads an Agent Task reviewed plan as a truthful read-only plan-only view", async () => {
    const reviewedPlan: ReviewedPlanRecord = {
      reviewed_plan_id: "reviewed-plan-only-1",
      project_id: "project-1",
      project_config_path: "work/projects/demo/project_config.yaml",
      dataset_index_path: "work/projects/demo/dataset_index.json",
      rawdata_dir: "work/projects/demo/rawdata",
      plan_hash: "sha256:plan-only",
      plan_path: "work/projects/demo/reviewed-plan-only-1.json",
      status: "REVIEWED",
      created_at: "2026-07-18T00:00:00Z",
      updated_at: "2026-07-18T00:00:00Z",
      approval_status: "PENDING",
      execution_status: "NOT_RUN",
      last_audit_id: null,
      last_execution_id: null,
      warnings: [],
      payload: {
        goal: "Prepare a resting-state preprocessing plan only",
        plan: {
          pipeline_id: "rsfmri_preproc_mvp",
          metadata: {
            plan_only: true,
            capability_level: "metadata_only",
            execution_enabled: false,
            rawdata_read_only: true,
          },
          nodes: [
            {
              id: "bids_validation_check",
              name: "BIDS validation check",
              backend: "python",
              params: {},
            },
            {
              id: "rsfmri_preprocessing_plan_stub",
              name: "Resting-state preprocessing plan",
              backend: "python",
              depends_on: ["bids_validation_check"],
              params: {},
            },
          ],
        },
        validation: {
          ok: true,
          errors: [],
          warnings: [
            { code: "NODE_CONTRACT_SCAFFOLDED", node_id: "bids_validation_check" },
            { code: "NODE_CONTRACT_SCAFFOLDED", node_id: "rsfmri_preprocessing_plan_stub" },
          ],
        },
        execution_status: "NOT_EXECUTED_PLAN_ONLY",
        execution_performed: false,
        rawdata_modified: false,
      },
    };
    pipelineApi.getProjectReviewedPlan.mockResolvedValue({
      ok: true,
      reviewed_plan: reviewedPlan,
    });

    renderWorkspace({ reviewedPlanId: "reviewed-plan-only-1" });

    expect(
      await screen.findByText("Prepare a resting-state preprocessing plan only"),
    ).toBeInTheDocument();
    expect(pipelineApi.getProjectReviewedPlan).toHaveBeenCalledWith(
      "http://localhost",
      "project-1",
      "reviewed-plan-only-1",
    );
    const planSteps = screen.getByRole("list", { name: "Plan pipeline steps" });
    expect(within(planSteps).getByText("BIDS validation check")).toBeInTheDocument();
    expect(within(planSteps).getByText("Resting-state preprocessing plan")).toBeInTheDocument();
    expect(screen.getAllByText("Reviewed · plan only")).toHaveLength(2);
    expect(screen.getByLabelText("Plan state machine")).toHaveTextContent("Not applicable");
    const reviewFacts = screen.getByLabelText("Plan review facts");
    expect(reviewFacts).toHaveTextContent("Validation errors0");
    expect(reviewFacts).toHaveTextContent("Validation notices2");
    expect(reviewFacts).toHaveTextContent("Not executed");
    expect(reviewFacts).toHaveTextContent("Unchanged");
    expect(screen.queryByText("No plan draft loaded")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open technical plan tools" }));
    expect(screen.getByText("Read-only reviewed plan")).toBeInTheDocument();
    expect(screen.queryByTestId("plan-review-console")).not.toBeInTheDocument();
  });

  it("projects a persisted Goal Contract candidate into the technical review console", async () => {
    const candidate = {
      goal_text: "Run reviewed preprocessing",
      goal_kind: "reviewed_execution_boundary",
      scope: { subject_ids: ["sub-001"], completeness_required: true },
      criteria: [
        {
          criterion_id: "terminal",
          criterion_type: "pipeline_terminal",
          target: "pipeline",
          required_evidence: ["pipeline_summary"],
        },
      ],
      minimum_capability_level: "computed",
      builder_source: "deterministic_goal_contract_builder",
    };
    const reviewedPlan: ReviewedPlanRecord = {
      reviewed_plan_id: "reviewed-needs-goal-review",
      project_id: "project-1",
      project_config_path: "work/projects/demo/project_config.yaml",
      dataset_index_path: "work/projects/demo/dataset_index.json",
      rawdata_dir: "work/projects/demo/rawdata",
      plan_hash: "sha256:needs-goal-review",
      plan_path: "work/projects/demo/reviewed-needs-goal-review.json",
      status: "NEEDS_GOAL_REVIEW",
      created_at: "2026-07-25T00:00:00Z",
      updated_at: "2026-07-25T00:00:00Z",
      approval_status: "PENDING",
      execution_status: "NOT_RUN",
      last_audit_id: null,
      last_execution_id: null,
      warnings: [],
      payload: {
        goal: "Run reviewed preprocessing",
        plan: draft().plan,
        validation: { ok: true, errors: [] },
        goal_contract_candidate: candidate,
        goal_contract_status: "needs_goal_review",
      },
    };
    pipelineApi.getProjectReviewedPlan.mockResolvedValue({
      ok: true,
      reviewed_plan: reviewedPlan,
    });

    renderWorkspace({ reviewedPlanId: reviewedPlan.reviewed_plan_id });

    await screen.findByText("Run reviewed preprocessing");
    fireEvent.click(screen.getByRole("button", { name: "Open technical plan tools" }));

    await screen.findByText("Read-only reviewed plan");
    expect(screen.getAllByText(/reviewed-needs-goal-review/)).not.toHaveLength(0);
  });
});
