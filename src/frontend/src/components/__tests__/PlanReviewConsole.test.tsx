import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectDetail } from "../../lib/types/project";
import type { PresetPlanDraft } from "../../types";
import PlanReviewConsole from "../PlanReviewConsole";
import {
  checkApprovalGate,
  executeReviewedDryRun,
  executeReviewedPlan,
  fetchToolCatalog,
  generatePlanFromGoal,
  listProjectReviewedPlans,
  saveReviewedPlan,
} from "../../lib/api/pipeline";

vi.mock("../../lib/api/pipeline", () => ({
  checkApprovalGate: vi.fn(),
  executeReviewedDryRun: vi.fn(),
  executeReviewedPlan: vi.fn(),
  fetchAuditRecord: vi.fn(),
  fetchToolCatalog: vi.fn(),
  generatePlanFromGoal: vi.fn(),
  listProjectReviewedPlans: vi.fn(),
  saveReviewedPlan: vi.fn(),
  validatePlan: vi.fn(),
}));

vi.mock("../../lib/api/client", () => ({ DEFAULT_API_BASE: "http://localhost" }));

const project: ProjectDetail = {
  id: "project-1",
  name: "Demo Project",
  study_id: "study-1",
  modality: "rs-fMRI",
  created_date: "2026-06-25",
  subjects_count: 1,
  current_pipeline_id: "not-selected",
  sequences: ["bold"],
  scans_count: 1,
  total_size: "1 MB",
  current_model_id: "model-1",
  metadata: {
    project_config_path: "work/projects/demo/project_config.yaml",
    rawdata_dir: "work/projects/demo/rawdata",
  },
};

function renderConsole(initialPresetDraft: PresetPlanDraft | null = null) {
  render(
    <PlanReviewConsole
      selectedProjectId="project-1"
      selectedProject={project}
      projectConfigPath="work/projects/demo/project_config.yaml"
      rawdataDir="work/projects/demo/rawdata"
      initialPresetDraft={initialPresetDraft}
    />,
  );
}

describe("PlanReviewConsole", () => {
  beforeEach(() => {
    vi.mocked(fetchToolCatalog).mockResolvedValue({ ok: true, count: 0, items: [] });
    vi.mocked(listProjectReviewedPlans).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      reviewed_plans: [],
    });
    vi.mocked(generatePlanFromGoal).mockReset();
    vi.mocked(saveReviewedPlan).mockReset();
    vi.mocked(checkApprovalGate).mockReset();
    vi.mocked(executeReviewedDryRun).mockReset();
    vi.mocked(executeReviewedPlan).mockReset();
  });

  it("keeps a restored reviewed-plan ID through dry-run so execution can be confirmed", async () => {
    const user = userEvent.setup();
    const reviewedDraft: PresetPlanDraft = {
      preset_id: "reviewed:reviewed-plan-1",
      project_id: "project-1",
      goal: "inspect registered data",
      plan: {
        pipeline_id: "data_inspection",
        project_context: { project_id: "project-1" },
        goal: "inspect registered data",
        nodes: [{ id: "data_inspection", backend: "python", params: {}, depends_on: [] }],
        metadata: {
          provider: "persisted",
          external_api_used: false,
          execution_enabled: true,
        },
      },
      validation: { ok: true, errors: [] },
      warnings: [],
      source: "reviewed_plan",
      reviewed_plan_id: "reviewed-plan-1",
      plan_hash: "reviewed-plan-hash",
      goal_contract_status: "reviewed",
    };
    vi.mocked(executeReviewedDryRun).mockResolvedValue({
      ok: true,
      status: "DRY_RUN_OK",
      dry_run: true,
      reviewed_plan_id: "reviewed-plan-1",
      execution: {
        executor_called: false,
        submitted: false,
        run_id: null,
      },
    });
    vi.mocked(executeReviewedPlan).mockResolvedValue({
      ok: true,
      status: "EXECUTION_SUBMITTED",
      dry_run: false,
      reviewed_plan_id: "reviewed-plan-1",
      execution: {
        executor_called: true,
        submitted: true,
        run_id: "run-1",
      },
    });

    renderConsole(reviewedDraft);

    await user.click(await screen.findByRole("button", { name: "Dry-run Execution Check" }));
    await screen.findByText(/Dry-run passed/i);

    await user.click(
      screen.getByLabelText(/request backend gated execution for the reviewed plan/i),
    );
    const executeButton = screen.getByRole("button", { name: "Execute Reviewed Plan" });
    expect(executeButton).toBeEnabled();

    await user.click(executeButton);
    await waitFor(() => expect(executeReviewedPlan).toHaveBeenCalledTimes(1));
    expect(executeReviewedDryRun).toHaveBeenCalledWith(
      "http://localhost",
      expect.objectContaining({ reviewed_plan_id: "reviewed-plan-1" }),
    );
    expect(executeReviewedPlan).toHaveBeenCalledWith(
      "http://localhost",
      expect.objectContaining({ reviewed_plan_id: "reviewed-plan-1" }),
    );
  });

  it("requires explicit Goal Contract review before a restored plan can reach dry-run", async () => {
    const user = userEvent.setup();
    const candidate = {
      goal_text: "inspect registered data",
      goal_kind: "reviewed_execution_boundary",
      scope: { subject_ids: ["sub-001"], completeness_required: true },
      criteria: [
        {
          criterion_id: "terminal",
          criterion_type: "pipeline_terminal",
          target: "pipeline",
          required_evidence: ["pipeline_summary"],
          expected: { statuses: ["SUCCESS", "COMPLETED"] },
          failure_semantics: "indeterminate_if_source_incomplete",
        },
      ],
      minimum_capability_level: "computed",
      builder_source: "deterministic_goal_contract_builder",
    };
    const reviewedDraft: PresetPlanDraft = {
      preset_id: "reviewed:needs-goal-review",
      project_id: "project-1",
      goal: "inspect registered data",
      plan: {
        pipeline_id: "data_inspection",
        project_context: { project_id: "project-1" },
        goal: "inspect registered data",
        nodes: [{ id: "data_inspection", backend: "python", params: {}, depends_on: [] }],
        metadata: {
          provider: "persisted",
          external_api_used: false,
          execution_enabled: true,
        },
      },
      validation: { ok: true, errors: [] },
      warnings: [],
      source: "reviewed_plan",
      reviewed_plan_id: "needs-goal-review",
      plan_hash: "unreviewed-hash",
      goal_contract_candidate: candidate,
      goal_contract_status: "needs_goal_review",
    };
    vi.mocked(saveReviewedPlan).mockResolvedValue({
      ok: true,
      reviewed_plan: {
        reviewed_plan_id: "reviewed-with-goal-contract",
        project_id: "project-1",
        project_config_path: "work/projects/demo/project_config.yaml",
        dataset_index_path: null,
        rawdata_dir: "work/projects/demo/rawdata",
        plan_hash: "reviewed-hash",
        plan_path: null,
        status: "REVIEWED",
        created_at: "2026-07-25T00:00:00Z",
        updated_at: "2026-07-25T00:00:00Z",
        approval_status: "not_requested",
        execution_status: "not_started",
        last_audit_id: null,
        last_execution_id: null,
        warnings: [],
        payload: {
          goal: "inspect registered data",
          goal_contract_status: "reviewed",
          goal_contract: { ...candidate, reviewed_actor: "frontend-user" },
        },
      },
    });

    renderConsole(reviewedDraft);

    expect(
      await screen.findByRole("heading", { name: "Goal Contract Review" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/\"goal_text\": \"inspect registered data\"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review Goal Contract and Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Dry-run Execution Check" })).toBeDisabled();

    await user.click(
      screen.getByLabelText(/I reviewed the Goal Contract goal, scope, criteria, and limitations/i),
    );
    await user.click(screen.getByRole("button", { name: "Review Goal Contract and Save" }));

    await waitFor(() => expect(saveReviewedPlan).toHaveBeenCalledTimes(1));
    expect(saveReviewedPlan).toHaveBeenCalledWith(
      "http://localhost",
      "project-1",
      expect.objectContaining({
        goal_contract_candidate: candidate,
        reviewed_actor: "frontend-user",
      }),
    );
    expect(await screen.findByText("Goal Contract reviewed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dry-run Execution Check" })).toBeEnabled();
  });

  it("does not persist generated plans missing reviewed-plan fields", async () => {
    const user = userEvent.setup();
    vi.mocked(generatePlanFromGoal).mockResolvedValue({
      ok: true,
      provider: "rule_based",
      goal: "motion correction",
      plan: {},
      validation: { ok: false, errors: [] },
      messages: [],
      warnings: [],
      errors: [],
    });

    renderConsole();

    await user.type(screen.getByRole("textbox"), "motion correction");
    await user.click(screen.getByRole("button", { name: "Generate Plan" }));

    expect(
      await screen.findByText(/Generated plan is invalid and was not persisted/i),
    ).toBeInTheDocument();
    expect(saveReviewedPlan).not.toHaveBeenCalled();
  });

  it("shows provider state and blocks real LLM generation when the API key is missing", async () => {
    const user = userEvent.setup();
    vi.mocked(generatePlanFromGoal).mockResolvedValue({
      ok: false,
      provider: "openai_compatible",
      goal: "motion correction",
      plan: {},
      validation: {},
      messages: [],
      warnings: [],
      errors: ["LLM_API_KEY_MISSING"],
    });

    renderConsole();

    expect(screen.getByRole("status")).toHaveTextContent(
      "Rule-based provider: local deterministic planner",
    );

    await user.selectOptions(screen.getByRole("combobox"), "openai_compatible");
    expect(screen.getByRole("status")).toHaveTextContent("requires MEDIMAGE_LLM_API_KEY");

    await user.type(screen.getByRole("textbox"), "motion correction");
    await user.click(screen.getByRole("button", { name: "Generate Plan" }));

    expect(
      await screen.findByText(/LLM provider disabled: API key not configured/i),
    ).toBeInTheDocument();
    expect(saveReviewedPlan).not.toHaveBeenCalled();
  });

  it("persists a minimal legal reviewed plan generated by the rule provider", async () => {
    const user = userEvent.setup();
    vi.mocked(generatePlanFromGoal).mockResolvedValue({
      ok: true,
      provider: "rule_based",
      goal: "motion correction",
      plan: {
        pipeline_id: "planned_motion_qc",
        project_context: { project_id: "project-1" },
        goal: "motion correction",
        nodes: [{ id: "data_inspection", backend: "python", params: {}, depends_on: [] }],
        metadata: {
          provider: "rule_based",
          external_api_used: false,
          execution_enabled: false,
        },
      },
      validation: { ok: true, errors: [] },
      planner_invocation: {
        schema_version: 1,
        invocation_id: "planner-invocation-1",
        provider_id: "rule_based",
        model_id: "deterministic-rules-v1",
        prompt_template_version: "planner-plan-v1",
        prompt_template_hash: "prompt-hash",
        input_schema_version: "planner-request-v1",
        input_hash: "input-hash",
        started_at: "2026-08-09T00:00:00Z",
        timeout_ms: 1000,
      },
      planner_evidence: {
        schema_version: 1,
        invocation_id: "planner-invocation-1",
        output_hash: "output-hash",
        validation_codes: [],
        fallback_used: false,
        failure_code: null,
        redacted_summary: "Rule planner produced one typed node.",
      },
      messages: [],
      warnings: [],
      errors: [],
    });
    vi.mocked(saveReviewedPlan).mockResolvedValue({
      ok: true,
      reviewed_plan: {
        reviewed_plan_id: "plan-1",
        project_id: "project-1",
        project_config_path: "work/projects/demo/project_config.yaml",
        dataset_index_path: null,
        rawdata_dir: "work/projects/demo/rawdata",
        plan_hash: "hash",
        plan_path: null,
        status: "reviewed",
        created_at: "2026-06-25T00:00:00Z",
        updated_at: "2026-06-25T00:00:00Z",
        approval_status: "not_requested",
        execution_status: "not_started",
        last_audit_id: null,
        last_execution_id: null,
        warnings: [],
        payload: {},
      },
    });

    renderConsole();

    await user.type(screen.getByRole("textbox"), "motion correction");
    await user.click(screen.getByRole("button", { name: "Generate Plan" }));

    await waitFor(() => expect(saveReviewedPlan).toHaveBeenCalledTimes(1));
    expect(saveReviewedPlan).toHaveBeenCalledWith(
      "http://localhost",
      "project-1",
      expect.objectContaining({
        plan: expect.objectContaining({
          project_context: expect.any(Object),
          goal: "motion correction",
          metadata: expect.any(Object),
        }),
        planner_invocation: expect.objectContaining({ invocation_id: "planner-invocation-1" }),
        planner_evidence: expect.objectContaining({ output_hash: "output-hash" }),
      }),
    );
  });

  it("sends native preprocessing safety acknowledgements to approval gate", async () => {
    const user = userEvent.setup();
    vi.mocked(generatePlanFromGoal).mockResolvedValue({
      ok: true,
      provider: "rule_based",
      goal: "native full preprocessing",
      plan: {
        pipeline_id: "native_full_preprocessing",
        project_context: { project_id: "project-1" },
        goal: "native full preprocessing",
        nodes: [
          {
            id: "native_preproc_full_execute",
            backend: "native_python",
            params: {},
            depends_on: [],
          },
        ],
        metadata: {
          provider: "rule_based",
          external_api_used: false,
          execution_enabled: false,
          native_preprocessing: true,
        },
      },
      validation: {
        ok: true,
        errors: [],
        approval_required_nodes: ["native_preproc_full_execute"],
        risk_summary: { requires_approval: true },
      },
      messages: [],
      warnings: [],
      errors: [],
    });
    vi.mocked(saveReviewedPlan).mockResolvedValue({
      ok: true,
      reviewed_plan: {
        reviewed_plan_id: "plan-native-1",
        project_id: "project-1",
        project_config_path: "work/projects/demo/project_config.yaml",
        dataset_index_path: null,
        rawdata_dir: "work/projects/demo/rawdata",
        plan_hash: "hash",
        plan_path: null,
        status: "reviewed",
        created_at: "2026-06-25T00:00:00Z",
        updated_at: "2026-06-25T00:00:00Z",
        approval_status: "not_requested",
        execution_status: "not_started",
        last_audit_id: null,
        last_execution_id: null,
        warnings: [],
        payload: {},
      },
    });
    vi.mocked(checkApprovalGate).mockResolvedValue({
      ok: true,
      execution_allowed: true,
      approval_required: true,
      approved: true,
      missing_approval_nodes: [],
      rejected_nodes: [],
      errors: [],
      warnings: [],
    });

    renderConsole();

    await user.type(screen.getByRole("textbox"), "native full preprocessing");
    await user.click(screen.getByRole("button", { name: "Generate Plan" }));

    await screen.findByText("Native Preprocessing Safety Acknowledgement");
    await user.click(screen.getByRole("button", { name: "Approve all required nodes" }));
    await user.click(screen.getByLabelText(/I acknowledge native full preprocessing will run/i));
    await user.click(screen.getByLabelText(/external tools will not be executed/i));
    await user.click(screen.getByLabelText(/rawdata must remain read-only/i));
    await user.click(screen.getByLabelText(/native preprocessing risks/i));
    await user.click(screen.getByLabelText(/subject\/session scope has been reviewed/i));
    await user.click(screen.getByRole("button", { name: "Check Approval Gate" }));

    await waitFor(() => expect(checkApprovalGate).toHaveBeenCalledTimes(1));
    expect(checkApprovalGate).toHaveBeenCalledWith(
      "http://localhost",
      expect.objectContaining({
        approval: expect.objectContaining({
          approved: true,
          approved_nodes: ["native_preproc_full_execute"],
          native_preprocessing_acknowledgement: true,
          no_external_tools_confirmed: true,
          rawdata_read_only_confirmed: true,
          risk_acknowledgement: true,
          subject_scope_confirmed: true,
        }),
      }),
    );
  });
});
