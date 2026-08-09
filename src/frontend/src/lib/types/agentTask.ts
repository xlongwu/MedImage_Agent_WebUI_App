export type AgentTaskPublicState =
  | "preparing"
  | "waiting_for_user"
  | "running"
  | "needs_attention"
  | "completed";

export type AgentTaskOutcome = "succeeded" | "partial" | "failed" | "canceled" | "indeterminate";

export type AgentTaskNextActionType =
  | "none"
  | "provide_input"
  | "revise_goal"
  | "answer_science_decision"
  | "approve_execution"
  | "approve_recovery"
  | "review_results"
  | "view_attention"
  | "contact_support";

export type AgentTaskProgressPhase =
  | "context"
  | "planning"
  | "plan_ready"
  | "data_preparation"
  | "execution"
  | "validation"
  | "recovery"
  | "complete";

export type AgentTaskDecisionKind =
  | "missing_input"
  | "goal_revision"
  | "subject_id"
  | "atlas"
  | "global_signal_regression"
  | "repetition_time"
  | "template"
  | "overwrite"
  | "experimental_backend"
  | "other";

export type AgentTaskEvidenceType =
  | "task_details"
  | "reviewed_plan"
  | "execution_ticket"
  | "run"
  | "observation"
  | "goal_evaluation"
  | "artifact"
  | "validation"
  | "provenance"
  | "audit"
  | "diagnosis"
  | "recovery";

export type AgentTaskEventSource =
  | "lifecycle"
  | "reviewed_plan"
  | "ticket"
  | "run"
  | "observation"
  | "goal_evaluation"
  | "diagnosis"
  | "recovery"
  | "artifact";

export type AgentTaskBackendSelection = {
  requested: "auto" | "cpu" | "gpu" | string;
  selected: "cpu" | "gpu" | string | null;
  fallback_reason: string | null;
};

export type AgentTaskDecisionOption = {
  id: string;
  label: string;
  description: string;
  recommended: boolean;
};

export type AgentTaskDecision = {
  item_id: string;
  kind: AgentTaskDecisionKind;
  question: string;
  impact: string;
  options: AgentTaskDecisionOption[];
  recommended_option: string | null;
  source?: "planner" | "memory_suggestion";
  memory_id?: string | null;
  recommendation_source?: string | null;
  answer_type: "option" | "text";
  required: boolean;
  evidence_refs: string[];
};

export type AgentTaskDecisionBatch = {
  batch_id: string;
  evidence_snapshot_hash: string;
  plan_hash_before: string | null;
  expires_at: string;
};

export type AgentTaskNextAction = {
  type: AgentTaskNextActionType;
  title: string;
  description: string | null;
  requires_user: boolean;
  decision_batch_id: string | null;
  disabled_reason: string | null;
};

export type AgentTaskProgress = {
  phase: AgentTaskProgressPhase;
  percent: number | null;
  completed_subjects: number | null;
  failed_subjects: number | null;
  excluded_subjects: number | null;
  total_subjects: number | null;
};

export type AgentTaskApprovalSection = {
  id: string;
  title: string;
  summary: string;
  warnings: string[];
};

export type AgentTaskApprovalSummary = {
  summary_hash: string;
  goal: string;
  dataset_summary: string;
  execution_summary: string;
  write_roots: string[];
  rawdata_read_only: boolean;
  external_tools: string[];
  limitations: string[];
  science_changes: string[];
  memory_context_hash?: string | null;
  memory_refs?: Record<string, unknown>[];
  memory_influence_summary?: string[];
  planning_inputs_hash?: string | null;
  revision_no?: number | null;
  parent_reviewed_plan_id?: string | null;
  parent_plan_hash?: string | null;
  revision_reason?: string | null;
  sections: AgentTaskApprovalSection[];
  expires_at: string | null;
};

export type AgentTaskArtifactSummary = {
  artifact_id: string;
  artifact_type: string;
  label: string;
  uri: string;
  checksum: string | null;
  capability_level: "unavailable" | "scaffolded" | "metadata_only" | "computed" | "validated";
  reload_status: "not_checked" | "passed" | "failed" | "unavailable";
};

export type AgentTaskResultSummary = {
  outcome: AgentTaskOutcome;
  title: string;
  summary: string;
  qc_summary: string | null;
  completed_subjects: number | null;
  failed_subjects: number | null;
  excluded_subjects: number | null;
  total_subjects: number | null;
  limitations: string[];
  recommended_action: string | null;
  artifacts: AgentTaskArtifactSummary[];
};

export type AgentResultCriterion = {
  criterion_id: string;
  status: "passed" | "failed" | "indeterminate";
  reason_code: string;
  evidence_ids: string[];
};

export type AgentResultExplanation = {
  outcome: AgentTaskOutcome;
  completed_subjects: number | null;
  failed_subjects: number | null;
  excluded_subjects: number | null;
  total_subjects: number | null;
  artifact_refs: AgentTaskArtifactSummary[];
  criteria: AgentResultCriterion[];
  limitations: string[];
  recommended_action: string | null;
  generated_text: string | null;
  generated_text_status: "not_requested" | "accepted" | "conflict_rejected";
};

export type AgentTaskRecoverySummary = {
  proposal_id: string;
  diagnosis: string;
  affected_subjects: string[];
  recommended_action: string;
  untouched_scope: string[];
  requires_new_plan: boolean;
  approval_summary_hash: string | null;
};

export type AgentTaskEvidenceLink = {
  id: string;
  type: AgentTaskEvidenceType;
  label: string;
  uri: string;
  available: boolean;
};

export type AgentTaskTechnicalDetails = {
  lifecycle_id: string;
  internal_state: string;
  reviewed_plan_id: string | null;
  plan_hash: string | null;
  planning_inputs_hash?: string | null;
  plan_revision_no?: number | null;
  parent_reviewed_plan_id?: string | null;
  parent_plan_hash?: string | null;
  revision_reason?: string | null;
  evidence_snapshot_hash?: string | null;
  goal_contract_id: string | null;
  goal_hash: string | null;
  ticket_id: string | null;
  run_id: string | null;
  observation_id: string | null;
  evaluation_id: string | null;
  backend: AgentTaskBackendSelection | null;
  node_ids: string[];
  memory_context_hash?: string | null;
  memory_refs?: Record<string, unknown>[];
  memory_retrieval_policy_version?: string | null;
  memory_status?: "disabled" | "enabled" | "partial" | null;
  memory_used_bytes?: number | null;
  memory_omitted_count?: number | null;
  memory_warnings?: string[];
  memory_available?: boolean | null;
  memory_generate_enabled?: boolean | null;
  memory_use_enabled?: boolean | null;
};

export type AgentHarnessStatus =
  | "READY"
  | "RUNNING"
  | "WAITING_FOR_USER"
  | "FINISHED"
  | "STOPPED"
  | "FAILED";

export type AgentHarnessSummary = {
  status: AgentHarnessStatus;
  model_calls_used: number;
  model_calls_limit: number;
  action_proposals_used: number;
  action_proposals_limit: number;
  steps_used: number;
  steps_limit: number;
  repairs_used: number;
  repairs_limit: number;
  recovery_attempts_used: number;
  recovery_attempts_limit: number;
  input_tokens_used: number | null;
  input_tokens_limit: number | null;
  output_tokens_used: number | null;
  output_tokens_limit: number | null;
  actual_provider: string | null;
  next_step: string | null;
  terminal_reason: string | null;
  latest_step_id: string | null;
  latest_step_summary: string | null;
  last_wake_reason: string | null;
  yield_count: number;
  fallback_from: string | null;
  fallback_to: string | null;
  fallback_reason: string | null;
};

export type AgentTaskResponse = {
  schema_version: 1;
  task_id: string;
  project_id: string;
  state: AgentTaskPublicState;
  outcome: AgentTaskOutcome | null;
  goal_summary: string;
  current_action: string;
  next_action: AgentTaskNextAction;
  progress: AgentTaskProgress;
  decisions: AgentTaskDecision[];
  decision_batch: AgentTaskDecisionBatch | null;
  approval_summary: AgentTaskApprovalSummary | null;
  result_summary: AgentTaskResultSummary | null;
  result_explanation?: AgentResultExplanation | null;
  recovery: AgentTaskRecoverySummary | null;
  evidence_links: AgentTaskEvidenceLink[];
  technical_details: AgentTaskTechnicalDetails | null;
  harness_summary?: AgentHarnessSummary | null;
  created_at: string;
  updated_at: string;
};

export type AgentTaskListResponse = {
  schema_version: 1;
  items: AgentTaskResponse[];
  total: number;
};

export type AgentTaskEvent = {
  event_id: string;
  task_id: string;
  project_id: string;
  source: AgentTaskEventSource;
  type: string;
  occurred_at: string;
  title: string;
  summary: string;
  evidence_uri: string | null;
};

export type AgentTaskEventPage = {
  schema_version: 1;
  items: AgentTaskEvent[];
  next_cursor: string | null;
};

export type CreateAgentTaskRequest = {
  goal: string;
  command_id: string;
  actor: string;
};

export type AnswerAgentTaskRequest = {
  batch_id: string;
  answers: { item_id: string; value: string }[];
  command_id: string;
  actor: string;
};

export type ApproveAgentTaskRequest = {
  approval_summary_hash: string;
  command_id: string;
};

export type CancelAgentTaskRequest = {
  command_id: string;
  actor: string;
  reason?: string;
};

export type ApproveAgentTaskRecoveryRequest = {
  command_id: string;
};
