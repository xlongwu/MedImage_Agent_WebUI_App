/**
 * Execute-Reviewed Status Explainer
 *
 * Pure helper that maps every /api/plans/execute-reviewed status code
 * to a structured UI object so the user can understand why execution is
 * allowed, blocked, or failed, and what action to take next.
 *
 * Safety: blocked-status next actions point to user-facing steps only
 * (enable env var, check approval, re-validate, inspect logs, etc.).
 * They never suggest editing backend code or disabling safety gates.
 */

export type ExecuteReviewedSeverity = "success" | "info" | "warning" | "error";

export type ExecuteReviewedStatusView = {
  /** The raw status string returned by the backend. */
  status: string;
  /** Short human-readable heading. */
  title: string;
  /** Semantic severity for colour-coding. */
  severity: ExecuteReviewedSeverity;
  /** One-paragraph explanation of what this status means. */
  explanation: string;
  /** Concrete next step the user can take.  Empty string when none is needed. */
  nextAction: string;
  /** Optional additional safety context for blocked statuses. */
  safetyNote?: string;
  /** Whether the user can re-run a dry-run check for this plan. */
  canRetryDryRun: boolean;
  /** Whether the user can attempt real execution. */
  canAttemptExecute: boolean;
};

export type DescribeOptions = {
  /** Was this a dry-run request? */
  dryRun?: boolean;
  /** Did the backend report execution_allowed? */
  executionAllowed?: boolean;
  /** Did the backend report would_execute? */
  wouldExecute?: boolean;
  /** Did the backend actually call the executor? */
  executorCalled?: boolean;
};

// ── Status-mapping table ────────────────────────────────────────────────────

type StatusEntry = Omit<ExecuteReviewedStatusView, "status">;

const STATUS_MAP: Record<string, StatusEntry> = {
  // ── Success ─────────────────────────────────────────────────────────────
  DRY_RUN_OK: {
    title: "Dry-run passed — execution would be allowed",
    severity: "success",
    explanation:
      "The backend re-validated the plan, checked the approval gate, ran the plan adapter, and verified execution policy. All gates passed. No pipeline was executed — this was a readiness check only.",
    nextAction:
      "Confirm execution by checking the confirmation box, then click 'Execute Reviewed Plan'.",
    canRetryDryRun: true,
    canAttemptExecute: true,
  },

  EXECUTION_SUBMITTED: {
    title: "Execution submitted successfully",
    severity: "success",
    explanation:
      "The backend passed all preflight gates (validation, approval, adapter, policy, safe allowlist, pipeline YAML write, audit write) and the pipeline executor completed. Check the run history panel for run details, summary, and artifacts.",
    nextAction: "",
    canRetryDryRun: true,
    canAttemptExecute: true,
  },

  EXECUTION_PREFLIGHT_READY: {
    title: "Preflight checks passed — ready to execute",
    severity: "success",
    explanation:
      "All pre-execution gates passed (env var, confirmation, audit, project config, project context, reviewed plan, validation, approval gate, plan adapter, policy, safe allowlist, pipeline YAML, audit record). The executor was not called yet — this is the preflight result.",
    nextAction:
      "The backend should proceed to call the executor. If it did not, check backend logs.",
    canRetryDryRun: true,
    canAttemptExecute: true,
  },

  // ── Warning (soft block — user can fix) ─────────────────────────────────
  REVIEWED_EXECUTION_DISABLED: {
    title: "Reviewed execution is disabled",
    severity: "warning",
    explanation:
      "The backend requires the environment variable MEDIMAGE_ENABLE_REVIEWED_EXECUTION to be set to '1' before it will execute any reviewed plan. This is a deliberate safety gate to prevent accidental execution.",
    nextAction: "Set MEDIMAGE_ENABLE_REVIEWED_EXECUTION=1 in the backend environment and restart.",
    safetyNote:
      "This gate exists to prevent accidental pipeline execution. Only enable it when you are ready to execute reviewed plans.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  AGENT_LIFECYCLE_ID_REQUIRED: {
    title: "Open this plan from its Agent Task",
    severity: "warning",
    explanation:
      "This reviewed plan is already bound to an Agent Task. The technical plan console cannot create a second lifecycle or impersonate an Agent retry.",
    nextAction: "Return to the Agent workspace and approve or retry the bound task there.",
    safetyNote:
      "The existing lifecycle, reviewed plan, approval summary, ticket, and run association remain authoritative.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  CONFIRMATION_REQUIRED: {
    title: "Confirmation required",
    severity: "warning",
    explanation:
      "The confirm_execution flag must be true before the backend will proceed. This ensures the user has explicitly acknowledged the intent to execute.",
    nextAction:
      "Check the 'I understand this will request backend gated execution' confirmation box.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  APPROVAL_GATE_BLOCKED: {
    title: "Approval gate blocked execution",
    severity: "warning",
    explanation:
      "The approval gate checked the plan against the supplied approval record and found one or more issues: missing required approvals, rejected nodes still present, or approval explicitly denied.",
    nextAction:
      "Review the approval gate result details. Ensure all required nodes are approved in the approval fields, and no rejected nodes remain in the plan.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  EXECUTION_POLICY_BLOCKED: {
    title: "Execution policy blocked",
    severity: "warning",
    explanation:
      "The plan adapter's execution policy blocked one or more nodes. This typically means the plan contains SPM, DPABI, GUI, manual-required, or uncataloged nodes that are not permitted in the current safe-execution mode.",
    nextAction:
      "Review the adapter policy details. Remove blocked node types (SPM/DPABI/GUI/manual/unknown) or switch to a pipeline that uses only safe allowlisted nodes.",
    safetyNote:
      "SPM, DPABI, and manual-required nodes are intentionally blocked in the current safe-execution path to prevent unverified external tool invocation.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  SAFE_EXECUTION_POLICY_BLOCKED: {
    title: "Safe execution policy blocked",
    severity: "warning",
    explanation:
      "The safe allowlist check failed. Either the plan contains GPU/contract nodes that are not on the safe allowlist, or the plan has no allowed nodes at all.",
    nextAction:
      "Replace GPU or unlisted contract nodes with safe allowlisted Python nodes. At minimum, the plan must contain at least one allowed Python node.",
    safetyNote:
      "GPU and certain contract nodes are blocked from real execution in the current release. They require explicit opt-in and are not part of the safe allowlist.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PIPELINE_WRITE_REQUIRES_AUDIT: {
    title: "Pipeline YAML write requires audit persistence",
    severity: "warning",
    explanation:
      "Writing the pipeline YAML to disk requires that audit persistence is also enabled. Both write_pipeline_yaml and persist_audit must be true.",
    nextAction: "Enable the 'Persist audit record' checkbox and retry.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  NATIVE_PREPROC_READINESS_BLOCKED: {
    title: "Native preprocessing inputs are not ready",
    severity: "warning",
    explanation:
      "The backend checked the native preprocessing inputs before execution and found required inputs missing. No pipeline was executed and rawdata was not modified.",
    nextAction:
      "Review the response errors, then supply the required template and atlas inputs or disable the dependent normalization, atlas, ROI time-series, and functional-connectivity stages before running the dry-run again.",
    safetyNote:
      "This preflight block prevents a long native preprocessing run that is known to be unable to complete.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  // ── Error (hard block — requires fix before retry) ──────────────────────
  AUDIT_REQUIRED: {
    title: "Audit persistence is required for execution",
    severity: "error",
    explanation:
      "Real execution (dry_run=false) requires persist_audit=true so that an immutable audit record is written before the executor runs.",
    nextAction:
      "Ensure persist_audit is true. In the frontend this is always sent as true for execution; if you see this error, check for an API client misconfiguration.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PROJECT_CONFIG_REQUIRED: {
    title: "Project config path is required",
    severity: "error",
    explanation:
      "The backend could not find a project_config_path. A valid project config YAML is required to locate work directories, rawdata references, and tool paths.",
    nextAction:
      "Ensure the project has a valid project_config.yaml. If using explicit demo mode, verify the demo config path is correct.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PROJECT_CONFIG_INVALID: {
    title: "Project config is invalid",
    severity: "error",
    explanation:
      "The backend could not load or validate the project config YAML. It may be missing, malformed, or missing required fields (work_dir, log_dir, spm_dir, dpabi_dir).",
    nextAction:
      "Check that the project config file exists and contains all required fields. Re-create the project if the config is corrupt.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PROJECT_CONTEXT_INVALID: {
    title: "Project context could not be resolved",
    severity: "error",
    explanation:
      "The backend could not load the project context for the given project_id and project_config_path. The project may not exist, or its metadata may be incomplete.",
    nextAction:
      "Verify the project exists in the dashboard. Re-create the project if metadata is missing or corrupt.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PROJECT_CONTEXT_MISMATCH: {
    title: "Plan does not match project context",
    severity: "error",
    explanation:
      "The plan's project_context block does not match the resolved project context. This can happen if the plan was generated for a different project or if the project's rawdata/dataset index has changed.",
    nextAction:
      "Re-generate the plan for the current project or restore a reviewed plan that matches the project context.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  REVIEWED_PLAN_INVALID: {
    title: "Reviewed plan is invalid for this project",
    severity: "error",
    explanation:
      "The reviewed plan could not be resolved or validated against the project. The plan hash, project ID, or reviewed_plan_id may not match the persisted record.",
    nextAction:
      "Save the plan again via 'Re-validate' to create a new reviewed plan snapshot, then retry.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  VALIDATION_FAILED: {
    title: "Plan validation failed",
    severity: "error",
    explanation:
      "The backend re-validated the plan and found one or more errors. The plan may contain invalid node references, missing required fields, or structural issues.",
    nextAction:
      "Review the validation errors in the details below. Fix the plan JSON and click 'Re-validate'.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PLAN_ADAPTER_FAILED: {
    title: "Plan adapter failed",
    severity: "error",
    explanation:
      "The plan adapter could not convert the reviewed plan into an executable pipeline. This typically indicates a structural issue in the plan that validation did not catch.",
    nextAction:
      "Inspect the adapter error details. Check for nodes with missing or malformed params. Re-generate the plan if the issue persists.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PIPELINE_YAML_REQUIRED: {
    title: "Pipeline YAML write is required for execution",
    severity: "error",
    explanation:
      "Real execution requires write_pipeline_yaml=true so the adapted pipeline can be written to disk before the executor runs.",
    nextAction:
      "Ensure write_pipeline_yaml is true. In the frontend this is always sent as true for execution; if you see this error, check for an API client misconfiguration.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  PIPELINE_WRITE_FAILED: {
    title: "Pipeline YAML write failed",
    severity: "error",
    explanation:
      "The backend attempted to write the adapted pipeline YAML to disk but the write operation failed. This could be due to disk permissions, a missing output directory, or a serialization error.",
    nextAction:
      "Check that the outputs directory is writable and that the pipeline can be serialized. Inspect backend logs for the specific write error.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  RUN_LINK_WRITE_FAILED: {
    title: "Run link could not be written to the store",
    severity: "error",
    explanation:
      "The backend created a run_link_id and run_id but could not persist the run link record to the SQLite store. This is a storage-layer error.",
    nextAction:
      "Check that the desktop SQLite store is accessible and not locked. Delete outputs/work/desktop/desktop_state.sqlite if it contains stale records and retry.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  AUDIT_WRITE_FAILED: {
    title: "Audit record could not be written",
    severity: "error",
    explanation:
      "The audit record write failed. Audit records are required before execution to maintain an immutable trace. Without a persisted audit record, execution is blocked.",
    nextAction:
      "Check that the audit record directory (outputs/reports/audit_records/) is writable. Inspect backend logs for the specific write error.",
    safetyNote:
      "Audit records are a safety requirement — execution is blocked when an audit record cannot be persisted.",
    canRetryDryRun: true,
    canAttemptExecute: false,
  },

  RUN_LINK_UPDATE_FAILED: {
    title: "Run link could not be updated after execution",
    severity: "error",
    explanation:
      "The executor completed but the backend could not update the run link with the final status, summary path, and executor result. The run link may be stuck in a stale status.",
    nextAction:
      "Check the run history panel for the run link. If it exists with a stale status, inspect the executor logs and consider re-running.",
    canRetryDryRun: true,
    canAttemptExecute: true,
  },

  EXECUTION_FAILED: {
    title: "Pipeline execution failed",
    severity: "error",
    explanation:
      "The pipeline executor was called but raised an exception or returned a failure status. The executor result should contain details about which node failed and why.",
    nextAction:
      "Inspect the executor result details below. Check run history for node-level state and logs. Fix the failing node and retry.",
    canRetryDryRun: true,
    canAttemptExecute: true,
  },

  REVIEWED_PLAN_NEEDS_GOAL_REVIEW: {
    title: "Goal Contract review required",
    severity: "warning",
    explanation:
      "The persisted plan has a candidate Goal Contract, but a human has not explicitly reviewed and hash-bound it to this exact plan.",
    nextAction:
      "Review the Goal Contract goal, scope, success criteria, evidence requirements, and limitations. Save the reviewed contract, then run the dry-run again.",
    canRetryDryRun: false,
    canAttemptExecute: false,
  },
};

// ── Fallback ────────────────────────────────────────────────────────────────

const FALLBACK: StatusEntry = {
  title: "Unknown status",
  severity: "info",
  explanation:
    "The backend returned a status code that the frontend status helper does not recognise. The raw response is shown below.",
  nextAction:
    "Inspect the raw response below. If this status persists, check the backend logs or update the frontend status helper.",
  canRetryDryRun: true,
  canAttemptExecute: false,
};

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Map a raw execute-reviewed status string to a structured UI view.
 *
 * @param status  The `status` field from a /api/plans/execute-reviewed response.
 * @param options Optional context (dryRun, executionAllowed, etc.) —
 *                currently reserved for future use; the primary mapping
 *                is keyed on the status string alone.
 */
export function describeExecuteReviewedStatus(
  status: string | undefined,
  _options?: DescribeOptions,
): ExecuteReviewedStatusView {
  const key = (status ?? "").trim();
  if (!key) {
    return {
      status: "(empty)",
      ...FALLBACK,
      title: "No status returned",
      explanation: "The backend response did not include a status field.",
    };
  }

  const entry = STATUS_MAP[key];
  if (entry) {
    return { status: key, ...entry };
  }

  return {
    status: key,
    ...FALLBACK,
    title: `Unknown status: ${key}`,
  };
}
