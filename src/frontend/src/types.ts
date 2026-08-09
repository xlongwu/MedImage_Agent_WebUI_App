export type ApiResult<T = unknown> = {
  ok?: boolean;
  error?: string;
} & T;

export type PipelineSummary = {
  pipeline_id: string;
  version: string;
  modality: string;
  description: string;
  nodes_total: number;
  nodes: Array<{
    id: string;
    name: string;
    backend: string;
    parallel_level: string;
    depends_on: string[];
  }>;
};

export type AgentPlanRequest = {
  agent_run_id: string;
  project_config_path: string;
  pipeline_path: string;
};

export type AgentExecuteRequest = AgentPlanRequest & {
  approved: boolean;
};

export type AgentRun = {
  ok: boolean;
  agent_run_id: string;
  plan: unknown | null;
  agent_summary: unknown | null;
  review_summary: string | null;
  proposed_memory_patch: string | null;
};

export type DatasetEvaluationReport = {
  ok: boolean;
  dataset_summary: unknown | null;
  subject_qc_table: string | null;
  exclusion_recommendations: string | null;
  report_markdown: string | null;
  report_html: string | null;
};

export type ProjectCreateRequest = {
  project_name: string;
  rawdata_dir: string;
  project_dir?: string | null;
  copy_mode?: "reference";
  run_inspection?: boolean;
  overwrite?: boolean;
};

export type ProjectCreateResponse = {
  ok: boolean;
  project_id: string;
  project_name: string;
  project_dir: string;
  rawdata_dir: string;
  project_config_path: string;
  dataset_index_path: string | null;
  diagnostics: Record<string, unknown>;
  warnings: string[];
  next_actions: string[];
};

export type ReviewedPlanRecord = {
  reviewed_plan_id: string;
  project_id: string;
  project_config_path: string;
  dataset_index_path: string | null;
  rawdata_dir: string | null;
  plan_hash: string;
  planner_invocation?: {
    schema_version: number;
    invocation_id: string;
    provider_id: string;
    model_id: string;
    prompt_template_version: string;
    prompt_template_hash: string;
    input_schema_version: string;
    input_hash: string;
    started_at: string;
    timeout_ms: number;
  } | null;
  planner_evidence?: {
    schema_version: number;
    invocation_id: string;
    output_hash: string | null;
    validation_codes: string[];
    fallback_used: boolean;
    failure_code: string | null;
    redacted_summary: string;
  } | null;
  plan_path: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  approval_status: string;
  execution_status: string;
  last_audit_id: string | null;
  last_execution_id: string | null;
  warnings: string[];
  payload: {
    plan?: Record<string, unknown>;
    validation?: Record<string, unknown>;
    goal?: string | null;
    provider?: string | null;
    [key: string]: unknown;
  };
};

export type RunLinkRecord = {
  run_link_id: string;
  project_id: string;
  reviewed_plan_id: string;
  run_id: string;
  dispatch_id?: string | null;
  task_id: string | null;
  pipeline_path: string | null;
  summary_path: string | null;
  project_config_path: string;
  audit_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  warnings: string[];
  payload: Record<string, unknown>;
};

export type RunSummaryPreview = {
  run_id?: string;
  status?: string;
  started_at?: string | null;
  finished_at?: string | null;
  nodes_total?: number | null;
  nodes_succeeded?: number | null;
  nodes_failed?: number | null;
  nodes_skipped?: number | null;
  warnings?: string[];
  outputs?: Record<string, unknown>;
  errors?: unknown[];
  failed_nodes?: Array<Record<string, unknown>>;
  raw?: Record<string, unknown>;
  raw_truncated?: boolean;
};

export type RunHealthLevel = "ok" | "warning" | "failed" | "unknown";

export type ProjectRunDetailResponse = {
  ok: boolean;
  run_link: RunLinkRecord;
  summary_preview?: RunSummaryPreview | null;
  summary_preview_error?: string | null;
  warnings?: string[];
};

export type RunArtifactRecord = {
  artifact_id: string;
  name: string;
  kind: string;
  path: string;
  relative_path: string;
  exists: boolean;
  size_bytes: number | null;
  modified_at: string | null;
  previewable: boolean;
  warnings: string[];
  source?: string;
  registered_artifact_id?: string;
  artifact_type?: string;
  stage_id?: string;
  subject_id?: string;
  registration_status?: string;
  suffix?: string;
  node_id?: string | null;
  category?: string | null;
  error_excerpt?: string | null;
  json_summary?: JsonPreviewSummary | null;
  qc_summary?: {
    status?: string | number | boolean | null;
    passed?: boolean | null;
    failed?: boolean | null;
    warnings?: string[];
    metrics?: Array<{ label: string; value: string }> | Record<string, unknown>;
    subject_id?: string | null;
    node_id?: string | null;
    error_message?: string | null;
    json_summary?: JsonPreviewSummary | null;
    truncated?: boolean;
  } | null;
};

export type CsvPreviewTable = {
  columns: string[];
  rows: string[][];
  row_count: number;
  displayed_rows: number;
  truncated: boolean;
  columns_truncated?: boolean;
};

export type JsonFieldSummary = {
  key: string;
  type: string;
  size?: number | null;
  keys?: string[];
  sample_types?: string[];
};

export type JsonMessageSummary = {
  count: number;
  sample: string[];
};

export type JsonPreviewSummary = {
  type: string;
  size?: number | null;
  top_level_keys: string[];
  status?: string | number | boolean | null;
  warnings: JsonMessageSummary;
  errors: JsonMessageSummary;
  field_summaries: JsonFieldSummary[];
};

export type ProjectRunArtifactsResponse = {
  ok: boolean;
  project_id: string;
  run_id: string;
  artifacts: RunArtifactRecord[];
  warnings: string[];
};

export type RunArtifactPreviewResponse = {
  ok: boolean;
  project_id: string;
  run_id: string;
  artifact_id: string;
  artifact: RunArtifactRecord;
  kind: string;
  path: string;
  exists: boolean;
  preview_type: "json" | "csv" | "markdown" | "text" | "log" | "metadata_only" | string;
  content: string | null;
  json: unknown | null;
  json_summary?: JsonPreviewSummary | null;
  csv?: CsvPreviewTable | null;
  truncated: boolean;
  warnings: string[];
  errors: string[];
};

export type RunListItem = {
  run_id: string;
  summary_path: string;
  status: string;
  pipeline_id?: string | null;
};

export type NodeStateSummary = {
  path: string;
  run_id?: string;
  subject?: string;
  node?: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
  outputs?: string[];
  errors?: string[];
  warnings?: string[];
  metrics?: Record<string, unknown>;
  stdout_log?: string | null;
  stderr_log?: string | null;
  result_json?: string | null;
  returncode?: number | null;
};

export type SubjectRunSummary = {
  subject_id: string;
  status: string;
  nodes: NodeStateSummary[];
};

export type RunInspection = {
  ok: boolean;
  run_id: string;
  summary_path: string;
  summary: unknown | null;
  project_states: NodeStateSummary[];
  subjects: SubjectRunSummary[];
  warnings: string[];
};

/** Response shape for POST /api/plans/execute-reviewed (dry_run or execute). */
export type ExecuteReviewedResponse = {
  ok?: boolean;
  status?: string;
  dry_run?: boolean;
  would_execute?: boolean;
  execution_allowed?: boolean;
  validation?: Record<string, unknown> | null;
  approval_gate?: Record<string, unknown> | null;
  adapter?: Record<string, unknown> | null;
  pipeline_yaml?: Record<string, unknown> | null;
  plan_summary?: Record<string, unknown> | null;
  project_config_path?: string | null;
  project_context?: Record<string, unknown> | null;
  execution?: {
    submitted?: boolean;
    run_id?: string | null;
    executor_called?: boolean;
  };
  audit?: {
    persisted?: boolean;
    audit_id?: string;
    audit_path?: string;
    event_type?: string;
    error?: string;
  };
  reviewed_plan_id?: string | null;
  run_link_id?: string | null;
  run_id?: string | null;
  pipeline_path?: string | null;
  summary_path?: string | null;
  executor_result?: Record<string, unknown> | null;
  errors?: unknown[];
  warnings?: unknown[];
};

/** Single event record from GET /api/projects/{id}/runs/{run_id}/events */
export type ProjectRunEventRecord = {
  timestamp?: string | null;
  level: string;
  source: string;
  message: string;
  node_id?: string | null;
  subject_id?: string | null;
  path?: string | null;
  metadata?: Record<string, unknown>;
};

export type ProjectRunEventsResponse = {
  ok: boolean;
  project_id: string;
  run_id: string;
  events: ProjectRunEventRecord[];
  warnings: string[];
  errors: string[];
};

/** Single log record from GET /api/projects/{id}/runs/{run_id}/logs */
export type ProjectRunLogRecord = {
  log_id: string;
  name: string;
  path: string;
  relative_path?: string | null;
  exists: boolean;
  size_bytes?: number | null;
  modified_at?: string | null;
  content?: string | null;
  truncated: boolean;
  warnings: string[];
};

export type ProjectRunLogsResponse = {
  ok: boolean;
  project_id: string;
  run_id: string;
  logs: ProjectRunLogRecord[];
  warnings: string[];
  errors: string[];
};

/** Single readiness check from GET /api/projects/{id}/data-readiness */
export type DataReadinessCheck = {
  name: string;
  status: "pass" | "warning" | "fail" | "unknown";
  message: string;
  details: Record<string, unknown>;
};

export type DataReadinessResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  checked_at: string;
  project_config_path?: string | null;
  dataset_index_path?: string | null;
  rawdata_dir?: string | null;
  import_count: number;
  image_source_count: number;
  subject_count: number;
  sequence_count: number;
  dicom_file_count: number;
  dicom_series_count: number;
  checks: DataReadinessCheck[];
  warnings: string[];
  errors: string[];
  next_actions: string[];
};

/** BIDS validation types */
export type BidsValidationIssue = {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  subject_id?: string | null;
  session_id?: string | null;
  modality?: string | null;
  file_path?: string | null;
  details: Record<string, unknown>;
};

export type BidsRepairSuggestion = {
  action_type:
    | "rename_suggestion"
    | "move_suggestion"
    | "metadata_suggestion"
    | "missing_file_suggestion"
    | "manual_review"
    | "conversion_required";
  title: string;
  description: string;
  source_path?: string | null;
  suggested_path?: string | null;
  command_preview?: string | null;
  safe_to_auto_apply: boolean;
  requires_user_review: boolean;
  related_issue_codes: string[];
};

export type BidsValidationResponse = {
  ok: boolean;
  project_id: string;
  status: "pass" | "warning" | "fail" | "unknown";
  checked_at: string;
  roots: string[];
  subject_count: number;
  session_count: number;
  nifti_file_count: number;
  sidecar_json_count: number;
  tsv_file_count: number;
  issues: BidsValidationIssue[];
  repair_suggestions: BidsRepairSuggestion[];
  warnings: string[];
  errors: string[];
  next_actions: string[];
};

/** Conversion dry-run types */
export type ConversionDryRunRequest = {
  source_import_ids?: string[];
  target_layout?: "bids";
  output_root_name?: string;
  subject_mapping_strategy?: "infer_from_dicom" | "infer_from_filename" | "manual_required";
  session_mapping_strategy?:
    | "none"
    | "infer_from_dicom"
    | "infer_from_filename"
    | "manual_required";
  include_dicom?: boolean;
  include_loose_nifti?: boolean;
};

export type ConversionSourceSummary = {
  source_id: string;
  source_type: "dicom" | "loose_nifti" | "bids" | "unknown";
  root: string;
  exists: boolean;
  file_count: number;
  subject_candidates: string[];
  series_count: number;
  warnings: string[];
};

export type ConversionMappingPreview = {
  source_path?: string | null;
  source_series_uid?: string | null;
  source_type: "dicom_series" | "nifti_file";
  subject_id?: string | null;
  session_id?: string | null;
  modality?: string | null;
  suffix?: string | null;
  task?: string | null;
  suggested_relative_path?: string | null;
  confidence: "high" | "medium" | "low" | "manual_required";
  warnings: string[];
};

export type ConversionDryRunResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  dry_run: boolean;
  checked_at: string;
  target_layout: "bids";
  output_root_name: string;
  output_root_preview?: string | null;
  source_summaries: ConversionSourceSummary[];
  mapping_preview: ConversionMappingPreview[];
  blocking_issues: string[];
  warnings: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** Pipeline preset types */
export type PipelinePresetNode = {
  id: string;
  name: string;
  stage: string;
  backend: string;
  requires_approval: boolean;
  executable: boolean;
  description: string;
  inputs: string[];
  outputs: string[];
  params: Record<string, unknown>;
  safety_notes: string[];
};

export type PipelinePreset = {
  preset_id: string;
  name: string;
  modality: string;
  description: string;
  version: string;
  nodes: PipelinePresetNode[];
  non_goals: string[];
  readiness_requirements: string[];
  safety_flags: Record<string, boolean>;
};

export type PipelinePresetInstantiateResponse = {
  ok: boolean;
  project_id: string;
  preset_id: string;
  plan: Record<string, unknown>;
  validation: Record<string, unknown>;
  warnings: string[];
  errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** Draft handoff from preset instantiation to the Plan workspace */
export type PresetPlanDraft = {
  preset_id: string;
  project_id: string;
  goal: string;
  plan: Record<string, unknown>;
  validation?: Record<string, unknown>;
  warnings?: string[];
  next_actions?: string[];
  source: "pipeline_preset" | "reviewed_plan";
  reviewed_plan_id?: string;
  plan_hash?: string;
  goal_contract_candidate?: Record<string, unknown>;
  goal_contract_status?: string;
};

/** Motion QC readiness types */
export type MotionQcInputCandidate = {
  subject_id?: string | null;
  session_id?: string | null;
  bold_path: string;
  relative_path?: string | null;
  has_sidecar: boolean;
  has_motion_params: boolean;
  motion_param_paths: string[];
  has_fd_column: boolean;
  fd_source_path?: string | null;
  warnings: string[];
};

export type MotionQcReadinessResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  checked_at: string;
  candidate_count: number;
  candidates: MotionQcInputCandidate[];
  missing_motion_param_count: number;
  fd_available_count: number;
  warnings: string[];
  errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** BOLD reference readiness types */
export type BoldReferenceCandidate = {
  subject_id?: string | null;
  session_id?: string | null;
  bold_path: string;
  relative_path?: string | null;
  dimensions: number[];
  voxel_spacing: number[];
  volume_count?: number | null;
  is_4d: boolean;
  has_sidecar: boolean;
  repetition_time?: number | null;
  task_name?: string | null;
  has_slice_timing: boolean;
  phase_encoding_direction?: string | null;
  reference_strategy: "middle_volume" | "single_volume" | "manual_required";
  warnings: string[];
};

export type BoldReferenceReadinessResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  checked_at: string;
  candidate_count: number;
  ready_count: number;
  warning_count: number;
  blocked_count: number;
  candidates: BoldReferenceCandidate[];
  warnings: string[];
  errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** rs-fMRI QC Planning Report types */
export type RsfmriQcPlanningReportArtifact = {
  kind: "json" | "markdown";
  path: string;
  exists: boolean;
  size_bytes?: number | null;
};

export type RsfmriQcPlanningReportResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  generated_at: string;
  report_dir: string;
  json_path: string;
  markdown_path: string;
  artifacts: RsfmriQcPlanningReportArtifact[];
  bold_reference_status: string;
  motion_qc_status: string;
  motion_metrics_status?: string | null;
  motion_metrics_parsed_count?: number;
  motion_metrics_fd_available_count?: number;
  motion_metrics_artifacts?: RsfmriQcPlanningReportArtifact[];
  bold_candidate_count: number;
  motion_candidate_count: number;
  ready_candidate_count: number;
  warning_count: number;
  blocked_count: number;
  warnings: string[];
  errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
  report_markdown?: string | null;
};

/** Motion metrics draft types */
export type MotionMetricsSubjectSummary = {
  subject_id?: string | null;
  session_id?: string | null;
  bold_path?: string | null;
  source_path: string;
  source_type: "spm_rp_txt" | "confounds_tsv" | "unknown";
  parsed: boolean;
  row_count: number;
  has_fd: boolean;
  volume_count_from_motion_rows?: number | null;
  max_abs_translation_mm?: number | null;
  mean_abs_translation_mm?: number | null;
  max_abs_rotation_rad?: number | null;
  mean_abs_rotation_rad?: number | null;
  fd_mean?: number | null;
  fd_max?: number | null;
  fd_over_0_2_count?: number | null;
  fd_over_0_5_count?: number | null;
  fd_over_0_2_fraction?: number | null;
  fd_over_0_5_fraction?: number | null;
  qc_flags: string[];
  warnings: string[];
};

export type MotionMetricsDraftArtifact = {
  kind: "json" | "markdown";
  path: string;
  exists: boolean;
  size_bytes?: number | null;
};

export type MotionMetricsDraftResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  generated_at: string;
  report_dir: string;
  json_path: string;
  markdown_path: string;
  artifacts: MotionMetricsDraftArtifact[];
  candidate_count: number;
  parsed_count: number;
  fd_available_count: number;
  summaries: MotionMetricsSubjectSummary[];
  warnings: string[];
  errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
  report_markdown?: string | null;
};

/** SPM realign dry-run types */
export type SpmRealignPredictedOutput = {
  kind:
    | "realigned_bold"
    | "mean_bold"
    | "motion_params"
    | "stdout_log"
    | "stderr_log"
    | "provenance_json"
    | "node_state_json";
  path: string;
  exists: boolean;
  would_overwrite: boolean;
  warning?: string | null;
};

export type SpmRealignInputPreview = {
  subject_id?: string | null;
  session_id?: string | null;
  bold_path: string;
  relative_path?: string | null;
  volume_count?: number | null;
  reference_strategy?: string | null;
  valid_for_realign: boolean;
  warnings: string[];
  predicted_outputs: SpmRealignPredictedOutput[];
};

export type SpmRealignDryRunResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  dry_run: boolean;
  checked_at: string;
  node_id: string;
  params: Record<string, unknown>;
  param_warnings: string[];
  param_errors: string[];
  input_count: number;
  ready_input_count: number;
  inputs: SpmRealignInputPreview[];
  output_root_preview?: string | null;
  environment_status?: string | null;
  approval_required: boolean;
  audit_required: boolean;
  execution_enabled: boolean;
  safe_allowlist_enabled: boolean;
  blocking_issues: string[];
  warnings: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** SPM realign wrapper skeleton types */
export type SpmRealignProvenancePreview = {
  command_template_id: string;
  node_id: string;
  dry_run_only: boolean;
  project_id: string;
  params: Record<string, unknown>;
  input_count: number;
  predicted_output_count: number;
  environment_status?: string | null;
  approval_required: boolean;
  audit_required: boolean;
  execution_enabled: boolean;
  safe_allowlist_enabled: boolean;
  warnings: string[];
};

export type SpmRealignOutputManifestItem = {
  kind: string;
  path: string;
  relative_path?: string | null;
  exists: boolean;
  size_bytes?: number | null;
  checksum_sha256?: string | null;
  modified_at?: string | null;
  required: boolean;
  verified: boolean;
  warnings: string[];
};

export type SpmRealignOutputManifest = {
  project_id: string;
  run_id: string;
  node_id: string;
  subject_id?: string | null;
  session_id?: string | null;
  output_root: string;
  items: SpmRealignOutputManifestItem[];
  missing_required_count: number;
  verified_count: number;
  warnings: string[];
  errors: string[];
};

export type SpmRealignWrapperSkeletonResponse = {
  ok: boolean;
  project_id: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  generated_at: string;
  node_id: string;
  command_template_id: string;
  dry_run: SpmRealignDryRunResponse | null;
  matlab_batch_preview: string;
  provenance_preview: SpmRealignProvenancePreview;
  output_manifests?: SpmRealignOutputManifest[];
  manifest_summary?: Record<string, unknown>;
  warnings: string[];
  errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** NIfTI QC Snapshot types */
export type NiftiQcStatus = "ready" | "warning" | "blocked" | "unknown";

export type NiftiImageQcRecord = {
  image_id: string;
  path: string;
  relative_path?: string | null;
  subject_id?: string | null;
  session_id?: string | null;
  modality?: string | null;
  suffix?: string | null;
  exists: boolean;
  readable: boolean;
  dimensions: number[];
  ndim?: number | null;
  volume_count?: number | null;
  voxel_spacing: number[];
  dtype?: string | null;
  orientation?: string | null;
  affine_determinant?: number | null;
  intensity_min?: number | null;
  intensity_max?: number | null;
  intensity_mean?: number | null;
  intensity_std?: number | null;
  zero_fraction?: number | null;
  nan_count: number;
  warnings: string[];
};

export type NiftiQcSnapshotResponse = {
  ok: boolean;
  project_id: string;
  status: NiftiQcStatus;
  checked_at: string;
  image_count: number;
  readable_count: number;
  unreadable_count: number;
  four_d_count: number;
  warning_count: number;
  images: NiftiImageQcRecord[];
  warnings: string[];
  errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** NIfTI slice thumbnail types */
export type NiftiThumbnailView = "axial" | "coronal" | "sagittal";

export type NiftiSliceThumbnail = {
  view: NiftiThumbnailView;
  width: number;
  height: number;
  slice_index: number;
  volume_index?: number | null;
  png_base64: string;
  intensity_min?: number | null;
  intensity_max?: number | null;
  warnings: string[];
};

export type NiftiThumbnailResponse = {
  ok: boolean;
  project_id: string;
  image_id: string;
  path: string;
  dimensions: number[];
  volume_count?: number | null;
  selected_volume_index?: number | null;
  thumbnails: NiftiSliceThumbnail[];
  warnings: string[];
  errors: string[];
  safety_flags: Record<string, boolean>;
};

/** QC Dashboard Report types */
export type QcDashboardModuleStatus = "ready" | "warning" | "blocked" | "unknown" | "not_run";

export type QcDashboardModuleSummary = {
  module_id: string;
  name: string;
  status: QcDashboardModuleStatus;
  ok: boolean;
  score?: number | null;
  summary: string;
  key_metrics: Record<string, unknown>;
  warnings: string[];
  errors: string[];
  next_actions: string[];
};

export type QcDashboardReportArtifact = {
  kind: "json" | "markdown";
  path: string;
  exists: boolean;
  size_bytes?: number | null;
};

export type QcDashboardReportResponse = {
  ok: boolean;
  project_id: string;
  status: QcDashboardModuleStatus;
  generated_at: string;
  report_dir: string;
  json_path: string;
  markdown_path: string;
  artifacts: QcDashboardReportArtifact[];
  modules: QcDashboardModuleSummary[];
  ready_count: number;
  warning_count: number;
  blocked_count: number;
  unknown_count: number;
  overall_warnings: string[];
  overall_errors: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
  report_markdown?: string | null;
  cache?: QcDashboardCacheSummary;
};

/** QC Dashboard cache types */
export type QcDashboardCacheMode = "prefer" | "refresh" | "off";
export type QcDashboardCacheStatus = "hit" | "miss" | "stale" | "disabled" | "error";

export type QcDashboardModuleCacheRecord = {
  module_id: string;
  status: QcDashboardCacheStatus;
  cache_key?: string | null;
  fingerprint?: string | null;
  module_version?: string | null;
  generated_at?: string | null;
  artifact_path?: string | null;
  hit: boolean;
  stale: boolean;
  warnings: string[];
  errors: string[];
};

export type QcDashboardCacheSummary = {
  mode: QcDashboardCacheMode;
  hit: boolean;
  fingerprint?: string | null;
  module_hits: Record<string, boolean>;
  module_records: QcDashboardModuleCacheRecord[];
  cache_warnings: string[];
  cache_errors: string[];
};

/** QC Dashboard fingerprint debug types */
export type RawdataFingerprintType = {
  ok: boolean;
  roots: string[];
  exists_count: number;
  missing_roots: string[];
  file_count: number;
  total_size_bytes: number;
  newest_mtime?: number | null;
  newest_mtime_iso?: string | null;
  relative_path_hash?: string | null;
  fingerprint?: string | null;
  truncated: boolean;
  max_files: number;
  warnings: string[];
  errors: string[];
};

export type QcDashboardFingerprintResponse = {
  ok: boolean;
  project_id: string;
  fingerprint: RawdataFingerprintType;
  roots: string[];
  warnings: string[];
  errors: string[];
  safety_flags: Record<string, boolean>;
};

/** Phase 3 run-state timeline types */
export type RunStateTimelineEvent = {
  timestamp?: string | null;
  state: string;
  source: string;
  message?: string | null;
  node_id?: string | null;
  metadata: Record<string, unknown>;
};

export type NodeStateTimelineRecord = {
  node_id: string;
  state: string;
  terminal: boolean;
  retry_eligible: boolean;
  reuse_eligible: boolean;
  warnings: string[];
  errors: string[];
  metadata: Record<string, unknown>;
};

export type ProjectRunStateTimelineResponse = {
  ok: boolean;
  project_id: string;
  run_id: string;
  current_run_state: string;
  terminal: boolean;
  retry_eligible: boolean;
  resume_eligible: boolean;
  events: RunStateTimelineEvent[];
  nodes: NodeStateTimelineRecord[];
  warnings: string[];
  errors: string[];
};

/** Phase 4C — DICOM conversion preflight / review types */

export type DicomConversionSafetyFlags = {
  rawdata_read_only: boolean;
  output_under_project: boolean;
  no_shell_string: boolean;
  command_template_only: boolean;
  approval_required: boolean;
  audit_required: boolean;
  conversion_disabled_by_default: boolean;
  env_flags_missing: boolean;
  no_spm_dpabi_matlab: boolean;
  clinical_use_prohibited: boolean;
  research_use_only: boolean;
};

export type Dcm2niixCommandTemplate = {
  tool: string;
  executable: string;
  input_dir: string;
  output_dir: string;
  filename_pattern: string;
  compress: string;
  bids_sidecar: boolean;
  create_bids: boolean;
  command_preview: string;
};

export type DicomConversionMapping = {
  subject_id?: string | null;
  modality: string;
  suffix?: string | null;
  task?: string | null;
  source_path: string;
  suggested_relative_path?: string | null;
  confidence: string;
};

export type DicomConversionPreflightResponse = {
  ok: boolean;
  project_id: string;
  status: string;
  conversion_disabled_by_default: boolean;
  conversion_backend: string;
  native_converter_available: boolean;
  native_converter_status: string;
  native_converter_version?: string | null;
  native_dependency_versions: Record<string, string>;
  /** @deprecated external converter is not used */
  dcm2niix_available: boolean;
  dcm2niix_status: string;
  dcm2niix_path?: string | null;
  dcm2niix_version?: string | null;
  env_enabled: boolean;
  missing_env_flags: string[];
  approval_required: boolean;
  audit_required: boolean;
  output_root_preview?: string | null;
  output_dir_safe: boolean;
  mapping_count: number;
  mappings: DicomConversionMapping[];
  command_templates: Dcm2niixCommandTemplate[];
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  safety_flags: DicomConversionSafetyFlags;
};

/** Phase 4K-0 — Release readiness types */

export type DicomConversionReleaseReadinessStatus =
  | "blocked"
  | "warning"
  | "ready_internal"
  | "ready_for_human_release_review";

export type DicomConversionDiskSpaceCheck = {
  output_root: string;
  free_bytes: number;
  estimated_required_bytes: number;
  required_multiplier: number;
  ok: boolean;
  warnings: string[];
  errors: string[];
};

export type DicomConversionRuntimePolicy = {
  timeout_seconds: number;
  cancellation_supported: boolean;
  resume_supported: boolean;
  retry_supported: boolean;
  max_subjects_per_run: number;
  warnings: string[];
  errors: string[];
};

export type DicomConversionReleaseReadinessReport = {
  ok: boolean;
  status: DicomConversionReleaseReadinessStatus;
  project_id: string;
  conversion_run_id: string;
  gate_status: string;
  gates_met: number;
  gates_total: number;
  disk_space: DicomConversionDiskSpaceCheck;
  runtime_policy: DicomConversionRuntimePolicy;
  rollback_ready: boolean;
  approval_audit_ready: boolean;
  public_endpoint_enabled: boolean;
  frontend_execute_enabled: boolean;
  spm_dpabi_matlab_enabled: boolean;
  full_preprocessing_enabled: boolean;
  human_release_approval_required: boolean;
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  safety_flags: Record<string, boolean>;
};

/** Phase 4E-0 — Plan persistence types */

export type DicomConversionRunReservation = {
  project_id: string;
  conversion_run_id: string;
  run_dir?: string | null;
  output_root?: string | null;
  approval_record_path?: string | null;
  audit_preview_path?: string | null;
  preflight_snapshot_path?: string | null;
  mapping_snapshot_path?: string | null;
  command_templates_path?: string | null;
  planned_manifest_path?: string | null;
  planned_provenance_path?: string | null;
  stdout_log_path?: string | null;
  stderr_log_path?: string | null;
  created_at?: string | null;
};

export type DicomConversionPlanPersistenceResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  conversion_run_id?: string | null;
  reservation?: DicomConversionRunReservation | null;
  gate_decision?: Record<string, unknown>;
  written_files: string[];
  warnings: string[];
  errors: string[];
  safety_flags: Record<string, boolean>;
};

/** Phase 4L-2 — Public execution endpoint request/response types */

export type DicomConversionPublicExecutionRequest = {
  conversion_run_id: string;
  release_approval_id: string;
  confirm_user_data_conversion: boolean;
  confirm_rawdata_readonly: boolean;
  confirm_research_use_only: boolean;
  confirm_no_clinical_use: boolean;
  confirm_rollback_available: boolean;
  confirm_disk_space_checked: boolean;
  confirm_public_execution_risk: boolean;
  requested_by: string;
  reason: string;
  dry_run_first: boolean;
  rollback_mode_on_failure: string;
};

export type DicomConversionPublicExecutionSafetyFlags = {
  conversion_disabled_by_default: boolean;
  env_flags_missing: boolean;
  public_execution_allowed: boolean;
  release_approval_obtained: boolean;
  release_readiness_ready: boolean;
  gates_32_of_32: boolean;
  approval_audit_package_present: boolean;
  rawdata_checksum_before_exists: boolean;
  rollback_plan_exists: boolean;
  disk_space_passed: boolean;
  output_root_safe: boolean;
  rawdata_read_only: boolean;
  spm_dpabi_matlab_disabled: boolean;
  full_preprocessing_disabled: boolean;
  human_release_approval_required: boolean;
  no_shell_execution: boolean;
};

export type DicomConversionPublicExecutionResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  conversion_run_id: string;
  execution_id: string;
  started_at: string | null;
  finished_at: string | null;
  output_root: string;
  output_manifest_path: string | null;
  execution_provenance_path: string | null;
  audit_execution_start_path: string | null;
  audit_execution_final_path: string | null;
  checksum_before_path: string | null;
  checksum_after_path: string | null;
  checksum_comparison_path: string | null;
  checksum_verified: boolean;
  rollback_plan_path: string | null;
  rollback_result_path: string | null;
  manifest_path: string | null;
  provenance_path: string | null;
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  safety_flags: DicomConversionPublicExecutionSafetyFlags;
};

/** 实现dcm2nii任务方案.md §13 — Prepare response types */

export type DicomConversionPrepareStatus =
  | "ready"
  | "review_required"
  | "blocked"
  | "disabled"
  | "partial"
  | "failed";

export type DicomConversionPrepareConfirmations = {
  mappings_reviewed: boolean;
  rawdata_readonly: boolean;
  research_use_only: boolean;
  no_clinical_use: boolean;
  native_converter: boolean;
  external_converter?: boolean;
  rollback_policy: boolean;
  risk_acknowledgement: boolean;
  approval_audit: boolean;
  public_endpoint: boolean;
  frontend_execute: boolean;
  spm_dpabi_matlab_disabled: boolean;
  confirm_execution: boolean;
};

export type DicomConversionPrepareSystemChecks = {
  preflight_ok: boolean;
  conversion_backend: string;
  native_converter_available: boolean;
  native_converter_version: string | null;
  native_dependency_versions: Record<string, string>;
  dcm2niix_available: boolean;
  dcm2niix_path: string | null;
  dcm2niix_version: string | null;
  dcm2niix_sha256: string | null;
  dcm2niix_strategy: string | null;
  mappings_complete: boolean;
  mapping_count: number;
  output_root_safe: boolean;
  output_root: string | null;
  rawdata_dir: string | null;
  project_dir: string | null;
  disk_space_ok: boolean;
  disk_free_bytes: number | null;
  disk_required_bytes: number | null;
  checksum_before_exists: boolean;
  checksum_before_path: string | null;
  rollback_plan_exists: boolean;
  rollback_plan_path: string | null;
  env_gates_ok: boolean;
  missing_env_flags: string[];
};

export type DicomConversionPrepareResponse = {
  ok: boolean;
  status: DicomConversionPrepareStatus;
  project_id: string;
  conversion_run_id: string;
  approval_id: string;
  technical_ready: boolean;
  approval_ready: boolean;
  execution_ready: boolean;
  next_action: string;
  system_checks: DicomConversionPrepareSystemChecks;
  operator_confirmations: DicomConversionPrepareConfirmations;
  missing_confirmations: string[];
  blocking_issues: string[];
  warnings: string[];
  errors: string[];
  run_dir: string | null;
  approval_record_path: string | null;
  release_approval_id: string;
  release_approval_record_path: string | null;
  release_approval_decision_path: string | null;
  audit_preview_path: string | null;
  preflight_snapshot_path: string | null;
  mapping_snapshot_path: string | null;
  command_templates_path: string | null;
  checksum_before_path: string | null;
  rollback_plan_path: string | null;
  review_package_path: string | null;
};

/** 实现dcm2nii任务方案.md §17 — Conversion result registration response */

export type DicomConversionResultRegistrationResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  conversion_run_id: string;
  output_root: string;
  execution_status: string;
  mapping_count: number;
  nifti_count: number;
  bold_count: number;
  t1w_count: number;
  subject_count: number;
  subjects: string[];
  manifest_path: string | null;
  provenance_path: string | null;
  checksum_verified: boolean;
  preprocessing_registered: boolean;
  project_metadata_updated: boolean;
  dashboard_refresh_required: boolean;
  viewer_refresh_required: boolean;
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  safety_flags: Record<string, boolean>;
};

/** Phase 4L-4 — Local UI state for execute panel */

export type DicomConversionExecutionUiState =
  | "hidden"
  | "disabled_info"
  | "confirming"
  | "submitting"
  | "succeeded"
  | "partial"
  | "failed"
  | "blocked";

/** Phase 5A — Preprocessing handoff types */

export type PreprocessingInputRegistrationResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  conversion_run_id: string;
  preprocessing_input_dir: string;
  rawdata_dir: string;
  subject_count: number;
  bold_count: number;
  t1w_count: number;
  nifti_count: number;
  sidecar_count: number;
  missing_t1w_subjects: string[];
  missing_bold_subjects: string[];
  subjects: string[];
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

export type PreprocessingStagePreview = {
  stage_id: string;
  name: string;
  backend: string;
  subject_level: boolean;
  requires_external_tool: boolean;
  enabled: boolean;
  optional: boolean;
  description: string;
  category?: string;
  default_enabled?: boolean;
  required_for_fc?: boolean;
  input_artifact_types?: string[];
  output_artifact_types?: string[];
  supported_backends?: string[];
  default_backend?: string;
  requires_approval?: boolean;
  requires_env_flags?: string[];
  can_run_in_ci?: boolean;
  scientific_status?: string;
  validation_status?: string;
};

export type PreprocessingPlanPreviewResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  stages: PreprocessingStagePreview[];
  stage_count: number;
  enabled_stage_count: number;
  execution_disabled: boolean;
  preprocessing_input_registered: boolean;
  warnings: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};

/** Phase 5B — Preprocessing run workspace */
export type PreprocessingRunCreateResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  preprocessing_run_id: string;
  run_dir: string;
  preprocessing_input_dir: string;
  artifact_registry_path: string;
  input_inventory: Record<string, unknown>;
  stage_count: number;
  python_stage_count: number;
  external_blocked_count: number;
  planned_stage_count: number;
  disabled_external_stage_count: number;
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};
export type PreprocessingStageStatus = {
  stage_id: string;
  name: string;
  status: string;
  backend: string;
  requires_external_tool: boolean;
  enabled: boolean;
  optional: boolean;
  category?: string;
  default_enabled?: boolean;
  required_for_fc?: boolean;
  input_artifact_types?: string[];
  output_artifact_types?: string[];
  supported_backends?: string[];
  default_backend?: string;
  requires_approval?: boolean;
  requires_env_flags?: string[];
  can_run_in_ci?: boolean;
  scientific_status?: string;
  validation_status?: string;
};
export type PreprocessingRunExecuteResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  preprocessing_run_id: string;
  completed_stages: string[];
  disabled_external_stages: string[];
  metadata_only_stages?: string[];
  preview_only_stages?: string[];
  stage_statuses: PreprocessingStageStatus[];
  input_inventory_path: string;
  qc_preflight_summary_path: string;
  manifest_path: string;
  warnings: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};
export type PreprocessingPipelineExecuteRequest = {
  pipeline_profile?: "fc_minimal" | "dparsfa_like" | "custom";
  start_from?: string;
  backend_policy?: {
    slice_timing?: string;
    motion_correction?: string;
    t1_coregistration?: string;
    segmentation?: string;
    normalization?: string;
    spatial_smoothing?: string;
    nuisance_regression?: string;
    temporal_filtering?: string;
    functional_connectivity?: string;
    alff_falff?: string;
    reho?: string;
  };
  stages?: Record<string, "enabled" | "disabled" | "auto">;
  atlas?: {
    atlas_path?: string;
    labels_path?: string;
    atlas_space?: string;
    allow_resample?: boolean;
  };
  nuisance?: {
    model?: string;
    include_wm_csf?: boolean;
    include_global_signal?: boolean;
    include_linear_trend?: boolean;
    include_intercept?: boolean;
  };
  filtering?: {
    low_hz?: number;
    high_hz?: number;
    fallback_tr?: number | null;
    tr?: number | null;
  };
  execution_limits?: {
    preview_limit?: number | null;
    max_subjects?: number | null;
  };
  confirmations?: {
    confirm_rawdata_readonly?: boolean;
    confirm_reviewed_execution?: boolean;
    confirm_external_tools_if_needed?: boolean;
    confirm_research_use_only?: boolean;
    confirm_no_clinical_use?: boolean;
  };
  approval?: Record<string, unknown> | null;
  resume?: boolean;
  rerun_policy?: "skip_succeeded" | "require_explicit" | "rerun_new_execution";
  derivatives_dir?: string;
  generate_report?: boolean;
  run_validation?: boolean;
};
export type PreprocessingPipelineStageResult = {
  stage_id: string;
  name: string;
  status: string;
  enabled: boolean;
  optional: boolean;
  backend: string;
  node_id: string;
  started_at: string;
  ended_at: string;
  skipped_reason: string;
  blocking_issues: string[];
  warnings: string[];
  errors: string[];
  output_artifact_ids: string[];
  result: Record<string, unknown>;
};
export type PreprocessingPipelineExecuteResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  preprocessing_run_id: string;
  execution_id: string;
  pipeline_profile: string;
  manifest_path: string;
  artifact_registry_path: string;
  report_path: string;
  validation_status: string;
  completed_stages: string[];
  skipped_stages: string[];
  blocked_stages: string[];
  failed_stages: string[];
  metadata_only_stages: string[];
  preview_only_stages: string[];
  stage_results: PreprocessingPipelineStageResult[];
  stage_statuses: PreprocessingStageStatus[];
  approval_gate: Record<string, unknown>;
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};
export type PreprocessingRunStatusResponse = {
  ok: boolean;
  project_id: string;
  preprocessing_run_id: string;
  run_dir: string;
  preprocessing_input_dir: string;
  status: string;
  stage_statuses: PreprocessingStageStatus[];
  artifacts: Record<string, string>;
  warnings: string[];
  safety_flags: Record<string, boolean>;
};
export type NativeFullPreprocConfirmations = {
  confirm_reviewed_native_execution?: boolean;
  confirm_rawdata_readonly?: boolean;
  confirm_no_external_tools?: boolean;
  confirm_research_use_only?: boolean;
  confirm_no_clinical_use?: boolean;
};
export type NativeCpuExecutionPolicy = {
  mode?: "serial" | "process" | "auto";
  max_subject_workers?: number | null;
  cpu_threads_per_worker?: number | null;
  memory_budget_bytes?: number | null;
  reserve_cpu_threads?: number | null;
  adaptive_replanning?: boolean;
};
export type NativeComputePolicy = {
  backend?: "cpu" | "gpu" | "auto";
  device?: "auto" | "cuda:0";
  precision?: "float32" | "float64";
  gpu_memory_budget_bytes?: number | null;
  max_gpu_jobs?: number | null;
  chunk_size?: number | null;
  allow_cpu_fallback?: boolean;
  adaptive_replanning?: boolean;
  stage_backends?: Record<
    | "alff"
    | "falff"
    | "temporal_filtering"
    | "nuisance_regression"
    | "functional_connectivity"
    | "smoothing"
    | "atlas_resampling",
    "cpu" | "gpu" | "auto"
  >;
};
export type NativeGpuDetection = {
  ok: boolean;
  cupy_available: boolean;
  gpu_available: boolean;
  device_id?: string | null;
  device_name?: string | null;
  free_vram_bytes?: number | null;
  total_vram_bytes?: number | null;
  cuda_runtime_version?: number | null;
  driver_version?: number | null;
  warnings: string[];
  errors: string[];
};
export type NativeFullPreprocRequest = {
  run_id?: string;
  conversion_run_id?: string;
  subject_id?: string;
  session_id?: string;
  output_dir?: string;
  input_bold?: string;
  sidecar_json?: string;
  t1w?: string;
  template?: string;
  atlas?: string;
  atlas_labels?: string;
  remove_first?: number;
  enable_slice_timing?: boolean;
  reference_time?: number | null;
  reference_slice_index?: number | null;
  reference_volume_index?: number;
  fd_threshold_mm?: number;
  head_radius_mm?: number;
  fwhm_mm?: number | number[];
  include_wm?: boolean;
  include_csf?: boolean;
  include_global_signal?: boolean;
  polynomial_order?: number;
  temporal_filter_type?: string;
  low_hz?: number | null;
  high_hz?: number | null;
  tr?: number | null;
  filtering_method?: string;
  reho_neighborhood?: number;
  atlas_name?: string;
  dparsf_config?: Record<string, unknown>;
  stage_overrides?: Record<string, boolean>;
  cpu_policy?: NativeCpuExecutionPolicy;
  compute_policy?: NativeComputePolicy;
  confirmations?: NativeFullPreprocConfirmations;
};
export type NativeFullStageApiResult = {
  stage_id: string;
  display_name: string;
  node_id: string;
  status: string;
  capability_level: string;
  validation_status: string;
  backend: string;
  input_artifacts: Record<string, unknown>[];
  output_artifacts: Record<string, unknown>[];
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  validation_errors: string[];
  result: Record<string, unknown>;
};
export type NativeFullPreprocResponse = {
  ok: boolean;
  status:
    | "planned"
    | "queued"
    | "running"
    | "cancel_requested"
    | "cancelled"
    | "interrupted"
    | "succeeded"
    | "partial"
    | "blocked"
    | "failed";
  dry_run: boolean;
  project_id: string;
  run_id: string;
  run_dir: string;
  backend: string;
  stage_graph: Record<string, unknown>[];
  stage_results: NativeFullStageApiResult[];
  completed_stages: string[];
  blocked_stages: string[];
  failed_stages: string[];
  skipped_stages: string[];
  metadata_only_stages: string[];
  warning_stages: string[];
  artifact_count: number;
  manifest_path: string;
  validation_report_path: string;
  final_report_path: string;
  warnings: string[];
  errors: string[];
  blocking_issues: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
  scheduler_mode?: "serial" | "process" | "auto";
  worker_count_requested?: number | null;
  worker_count_calculated?: number;
  worker_count_used?: number;
  threads_per_worker_calculated?: number;
  resource_decision?: Record<string, unknown>;
  subject_execution?: Record<string, unknown>[];
  progress_url?: string;
  started_at?: string;
  finished_at?: string;
  runtime_seconds?: number | null;
};
/** Phase 5E — Sandbox execution */
export type SpmSandboxExecutionResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  preprocessing_run_id: string;
  dry_run_id: string;
  execution_id: string;
  execution_dir: string;
  sandbox_input_dir: string;
  sandbox_output_dir: string;
  subjects_total: number;
  subjects_succeeded: number;
  command_template_path: string;
  stdout_log_path: string;
  stderr_log_path: string;
  manifest_path: string;
  provenance_path: string;
  warnings: string[];
  blocking_issues: string[];
  safety_flags: Record<string, boolean>;
};
/** Phase 5F */
export type StageOutputRegistrationResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  preprocessing_run_id: string;
  execution_id: string;
  registered_stage_output_id: string;
  stage_output_dir: string;
  next_stage_input_dir: string;
  subject_count: number;
  registered_bold_outputs: string[];
  missing_subject_outputs: string[];
  motion_files: string[];
  mean_images: string[];
  warnings: string[];
  blocking_issues: string[];
  next_actions: string[];
  safety_flags: Record<string, boolean>;
};
/** Phase 5K */
export type NuisanceSandboxExecutionResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  preprocessing_run_id: string;
  dry_run_id: string;
  execution_id: string;
  execution_dir: string;
  sandbox_input_dir: string;
  sandbox_output_dir: string;
  subjects_total: number;
  subjects_succeeded: number;
  regressor_design_path: string;
  stdout_log_path: string;
  stderr_log_path: string;
  manifest_path: string;
  provenance_path: string;
  subject_status_path: string;
  warnings: string[];
  blocking_issues: string[];
  safety_flags: Record<string, boolean>;
};
export type FilteringDryRunResponse = {
  ok: boolean;
  status: string;
  project_id: string;
  preprocessing_run_id: string;
  dry_run_id: string;
  dry_run_dir: string;
  subject_count: number;
  planned_subjects: string[];
  functional_input_count: number;
  filter_design_paths: string[];
  planned_output_paths: string[];
  warnings: string[];
  blocking_issues: string[];
  safety_flags: Record<string, boolean>;
};
