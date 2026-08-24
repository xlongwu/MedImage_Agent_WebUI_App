from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.backend.app.schemas.planner_provenance import PlannerEvidence, PlannerInvocation

TaskStatus = Literal["running", "completed", "failed", "pending", "disconnected"]
ExecutionMode = Literal["simulated", "external_smoke", "rsfmri_python"]
ExternalSmokeMode = Literal["manual_package", "approved_smoke"]
DatasetType = Literal["nifti", "dicom", "bids"]
ImagePlane = Literal["axial", "sagittal", "coronal"]
ImageValidationSeverity = Literal["info", "warning", "error"]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "medimage-agent-backend"
    version: str = "0.1.0"


class ProjectAgentTaskSummary(BaseModel):
    task_id: str
    state: Literal["preparing", "waiting_for_user", "running", "needs_attention", "completed"]
    outcome: Literal["succeeded", "partial", "failed", "canceled", "indeterminate"] | None = None
    goal_summary: str
    current_action: str
    current_action_code: str
    requires_user: bool
    result_title: str | None = None
    recent_activity: str
    updated_at: str


class ProjectSummary(BaseModel):
    id: str
    name: str
    study_id: str
    modality: str
    created_date: str
    subjects_count: int
    current_pipeline_id: str
    latest_agent_task: ProjectAgentTaskSummary | None = None


class ProjectDetail(ProjectSummary):
    sequences: list[str]
    scans_count: int
    total_size: str
    current_model_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewedPlanRecord(BaseModel):
    reviewed_plan_id: str
    project_id: str
    project_config_path: str
    dataset_index_path: str | None = None
    rawdata_dir: str | None = None
    plan_hash: str
    revision_no: int = Field(default=1, ge=1)
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: Literal[
        "initial", "decision_answered", "goal_revised", "recovery_replan"
    ] = "initial"
    planning_inputs_hash: str | None = None
    evidence_snapshot_hash: str | None = None
    memory_context_hash: str | None = None
    memory_context_refs: list[dict[str, Any]] = Field(default_factory=list)
    memory_retrieval_policy_version: str | None = None
    planner_invocation: PlannerInvocation | None = None
    planner_evidence: PlannerEvidence | None = None
    plan_path: str | None = None
    status: str = "REVIEWED"
    created_at: str
    updated_at: str
    approval_status: str = "PENDING"
    execution_status: str = "NOT_RUN"
    last_audit_id: str | None = None
    last_execution_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class RunLinkRecord(BaseModel):
    run_link_id: str
    project_id: str
    reviewed_plan_id: str
    run_id: str
    dispatch_id: str | None = None
    task_id: str | None = None
    pipeline_path: str | None = None
    summary_path: str | None = None
    project_config_path: str
    audit_id: str | None = None
    status: str = "REQUESTED"
    created_at: str
    updated_at: str
    warnings: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class StudyOverview(BaseModel):
    project_id: str
    study_id: str
    study_name: str
    modality: str
    sequences: list[str]
    subjects: int
    scans: int
    total_size: str
    date: str
    dicom_subjects: int = 0
    dicom_series: int = 0
    dicom_files: int = 0


class DatasetSummary(BaseModel):
    project_id: str
    subjects: int
    scans: int
    total_size: str
    health_status: str
    dicom_subjects: int = 0
    dicom_series: int = 0
    dicom_files: int = 0


class ModelStatus(BaseModel):
    project_id: str
    model_name: str
    version: str
    status: str
    dice_score: float
    last_trained: str
    metrics: dict[str, float]


class TaskLogEntry(BaseModel):
    id: str
    run_name: str
    pipeline: str
    dataset: str
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    started_at: str
    duration: str
    owner: str
    logs: list[str] = Field(default_factory=list)
    result_path: str | None = None
    execution_mode: ExecutionMode = "simulated"


class TaskDetail(TaskLogEntry):
    project_id: str
    pipeline_id: str
    model_id: str
    input_sequences: list[str]
    output_type: str
    updated_at: str


class TaskEvent(BaseModel):
    id: int
    task_id: str
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    message: str
    timestamp: str
    result_path: str | None = None
    source: str = "task_manager"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalRecord(BaseModel):
    approval_id: str
    task_id: str
    approved: bool
    approved_by: str
    approved_at: str
    approval_scope: str = "external_smoke_approved_run"
    safety_flags: dict[str, bool] = Field(default_factory=dict)


class TaskApprovalRequest(BaseModel):
    approved: bool = False
    approved_by: str = ""
    approval_scope: str = "external_smoke_approved_run"
    safety_flags: dict[str, bool] = Field(
        default_factory=lambda: {
            "rawdata_read_only": True,
            "no_dparsf_blackbox": True,
            "matlab_external_execution": True,
        }
    )


class TaskApprovalResponse(BaseModel):
    ok: bool
    approval: ApprovalRecord | None = None
    message: str


class TaskDiagnosticsResponse(BaseModel):
    ok: bool
    task_id: str
    status: TaskStatus
    diagnosis: list[dict[str, Any]] = Field(default_factory=list)
    external_tool_results: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    approval: ApprovalRecord | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TaskArtifactsResponse(BaseModel):
    ok: bool
    task_id: str
    result_path: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    approval: ApprovalRecord | None = None
    errors: list[str] = Field(default_factory=list)


class TaskAuditPackageResponse(BaseModel):
    ok: bool
    task_id: str
    generated_at: str
    package_dir: str
    report_path: str
    json_path: str
    report_text: str
    artifacts: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class DatasetImportRequest(BaseModel):
    project_id: str
    path: str
    type: DatasetType


class DatasetImportResponse(BaseModel):
    success: bool
    dataset_id: str
    message: str
    manifest_path: str | None = None
    image_source_count: int = 0
    validation_report_path: str | None = None
    validation_report_text: str | None = None
    validation_issue_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class DatasetImportRecord(BaseModel):
    dataset_id: str
    project_id: str
    path: str
    dataset_type: DatasetType
    created_at: str
    exists: bool = False


class DatasetImportHistoryResponse(BaseModel):
    ok: bool
    project_id: str
    imports: list[DatasetImportRecord] = Field(default_factory=list)


class DatasetDiagnosticsPackageResponse(BaseModel):
    ok: bool
    project_id: str
    generated_at: str
    package_dir: str
    report_path: str
    json_path: str
    zip_path: str
    checksum_path: str | None = None
    report_text: str
    checksums: dict[str, str] = Field(default_factory=dict)
    safety_flags: dict[str, bool] = Field(default_factory=dict)
    file_inventory: dict[str, Any] = Field(default_factory=dict)
    manifest_path: str | None = None
    validation_report_path: str | None = None
    import_count: int = 0
    image_source_count: int = 0
    validation_issue_count: int = 0
    dicom_preflight_report_path: str | None = None
    dicom_preflight_json_path: str | None = None
    dicom_file_count: int = 0
    dicom_series_count: int = 0
    errors: list[str] = Field(default_factory=list)


class DatasetDiagnosticsPackageStatusResponse(BaseModel):
    ok: bool
    project_id: str
    latest: DatasetDiagnosticsPackageResponse | None = None
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class DatasetDiagnosticsPackageVerifyResponse(BaseModel):
    ok: bool
    project_id: str
    checked_at: str
    zip_path: str | None = None
    checksum_path: str | None = None
    checked_files: int = 0
    passed_files: int = 0
    failed_files: list[str] = Field(default_factory=list)
    missing_files: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DicomSeriesSummary(BaseModel):
    series_instance_uid: str
    study_instance_uid: str | None = None
    subject_id: str | None = None
    modality: str | None = None
    series_description: str | None = None
    protocol_name: str | None = None
    sequence_name: str | None = None
    manufacturer: str | None = None
    magnetic_field_strength: float | None = None
    repetition_time: float | None = None
    echo_time: float | None = None
    flip_angle: float | None = None
    rows: int | None = None
    columns: int | None = None
    instances: int = 0
    sample_file: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DicomPreflightResponse(BaseModel):
    ok: bool
    project_id: str
    checked_at: str
    roots: list[str] = Field(default_factory=list)
    dicom_file_count: int = 0
    sampled_file_count: int = 0
    series_count: int = 0
    subjects: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    series: list[DicomSeriesSummary] = Field(default_factory=list)
    report_path: str | None = None
    json_path: str | None = None
    report_text: str | None = None
    safety_flags: dict[str, bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PipelineRunRequest(BaseModel):
    project_id: str
    pipeline_id: str
    model_id: str
    input_sequences: list[str]
    output_type: str
    execution_mode: ExecutionMode = "simulated"
    external_smoke_mode: ExternalSmokeMode = "manual_package"
    approved: bool = False
    approved_by: str | None = None
    dpabi_function: str = "y_Smooth"


class PipelineRunResponse(BaseModel):
    task_id: str
    status: TaskStatus


class TaskStreamMessage(BaseModel):
    task_id: str
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    message: str
    timestamp: str
    result_path: str | None = None


class AssistantChatRequest(BaseModel):
    project_id: str
    message: str


class AssistantChatResponse(BaseModel):
    reply: str


class ImagePreviewResponse(BaseModel):
    project_id: str
    subject_id: str | None = None
    sequence: str
    plane: ImagePlane = "axial"
    preview_url: str | None = None
    message: str
    source: str = "fallback"
    source_path: str | None = None
    slice_index: int | None = None
    slice_count: int | None = None
    dimensions: list[int] = Field(default_factory=list)


class ImageSourceFile(BaseModel):
    subject_id: str
    sequence: str
    file_path: str
    relative_path: str
    format: str = "nifti"
    session_id: str | None = None
    source_root: str | None = None
    size_bytes: int | None = None
    modified_at: str | None = None
    dimensions: list[int] = Field(default_factory=list)
    voxel_spacing: list[float] = Field(default_factory=list)
    plane_slice_counts: dict[ImagePlane, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ImageSourceSubject(BaseModel):
    subject_id: str
    sequences: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    file_details: list[ImageSourceFile] = Field(default_factory=list)


class ImageSourcesResponse(BaseModel):
    project_id: str
    subjects: list[ImageSourceSubject] = Field(default_factory=list)
    sequences: list[str] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list)
    manifest: list[ImageSourceFile] = Field(default_factory=list)
    manifest_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ImageValidationIssue(BaseModel):
    severity: ImageValidationSeverity
    code: str
    message: str
    subject_id: str | None = None
    sequence: str | None = None
    file_path: str | None = None


class ImageValidationReport(BaseModel):
    ok: bool
    project_id: str
    status: Literal["pass", "warning", "fail"]
    checked_at: str
    source_count: int = 0
    subject_count: int = 0
    sequence_count: int = 0
    expected_sequences: list[str] = Field(default_factory=list)
    issues: list[ImageValidationIssue] = Field(default_factory=list)
    report_path: str | None = None
    json_path: str | None = None
    manifest_path: str | None = None
    report_text: str | None = None


# ── Run events and logs ──────────────────────────────────────────────────────


class ProjectRunEventRecord(BaseModel):
    timestamp: str | None = None
    level: str = "info"
    source: str = "run"
    message: str
    node_id: str | None = None
    subject_id: str | None = None
    path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectRunEventsResponse(BaseModel):
    ok: bool
    project_id: str
    run_id: str
    events: list[ProjectRunEventRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProjectRunLogRecord(BaseModel):
    log_id: str
    name: str
    path: str
    relative_path: str | None = None
    exists: bool = False
    size_bytes: int | None = None
    modified_at: str | None = None
    content: str | None = None
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class ProjectRunLogsResponse(BaseModel):
    ok: bool
    project_id: str
    run_id: str
    logs: list[ProjectRunLogRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ── Data Readiness ──────────────────────────────────────────────────────────


class DataReadinessCheck(BaseModel):
    name: str
    status: Literal["pass", "warning", "fail", "unknown"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DataReadinessResponse(BaseModel):
    ok: bool
    project_id: str
    status: Literal["ready", "warning", "blocked", "unknown"]
    checked_at: str
    project_config_path: str | None = None
    dataset_index_path: str | None = None
    rawdata_dir: str | None = None
    import_count: int = 0
    image_source_count: int = 0
    subject_count: int = 0
    sequence_count: int = 0
    dicom_file_count: int = 0
    dicom_series_count: int = 0
    checks: list[DataReadinessCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


# ── BIDS Validation ─────────────────────────────────────────────────────────


BidsIssueSeverity = Literal["info", "warning", "error"]
BidsRepairActionType = Literal[
    "rename_suggestion",
    "move_suggestion",
    "metadata_suggestion",
    "missing_file_suggestion",
    "manual_review",
    "conversion_required",
]


class BidsValidationIssue(BaseModel):
    severity: BidsIssueSeverity
    code: str
    message: str
    subject_id: str | None = None
    session_id: str | None = None
    modality: str | None = None
    file_path: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BidsRepairSuggestion(BaseModel):
    action_type: BidsRepairActionType
    title: str
    description: str
    source_path: str | None = None
    suggested_path: str | None = None
    command_preview: str | None = None
    safe_to_auto_apply: bool = False
    requires_user_review: bool = True
    related_issue_codes: list[str] = Field(default_factory=list)


class BidsValidationResponse(BaseModel):
    ok: bool
    project_id: str
    status: Literal["pass", "warning", "fail", "unknown"]
    checked_at: str
    roots: list[str] = Field(default_factory=list)
    subject_count: int = 0
    session_count: int = 0
    nifti_file_count: int = 0
    sidecar_json_count: int = 0
    tsv_file_count: int = 0
    issues: list[BidsValidationIssue] = Field(default_factory=list)
    repair_suggestions: list[BidsRepairSuggestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


# ── Conversion Dry-Run ──────────────────────────────────────────────────────


class ConversionDryRunRequest(BaseModel):
    source_import_ids: list[str] = Field(default_factory=list)
    target_layout: Literal["bids"] = "bids"
    output_root_name: str = "converted_bids"
    subject_mapping_strategy: Literal[
        "infer_from_dicom",
        "infer_from_filename",
        "manual_required"
    ] = "infer_from_filename"
    session_mapping_strategy: Literal[
        "none",
        "infer_from_dicom",
        "infer_from_filename",
        "manual_required"
    ] = "none"
    include_dicom: bool = True
    include_loose_nifti: bool = True


class ConversionSourceSummary(BaseModel):
    source_id: str
    source_type: Literal["dicom", "loose_nifti", "bids", "unknown"]
    root: str
    exists: bool
    file_count: int = 0
    subject_candidates: list[str] = Field(default_factory=list)
    series_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class ConversionMappingPreview(BaseModel):
    source_path: str | None = None
    source_series_uid: str | None = None
    source_type: Literal["dicom_series", "nifti_file"]
    subject_id: str | None = None
    session_id: str | None = None
    modality: str | None = None
    suffix: str | None = None
    task: str | None = None
    suggested_relative_path: str | None = None
    confidence: Literal["high", "medium", "low", "manual_required"] = "manual_required"
    warnings: list[str] = Field(default_factory=list)


class ConversionDryRunResponse(BaseModel):
    ok: bool
    project_id: str
    status: Literal["ready", "warning", "blocked", "unknown"]
    dry_run: bool = True
    checked_at: str
    target_layout: Literal["bids"] = "bids"
    output_root_name: str
    output_root_preview: str | None = None
    source_summaries: list[ConversionSourceSummary] = Field(default_factory=list)
    mapping_preview: list[ConversionMappingPreview] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── Motion QC Readiness ─────────────────────────────────────────────────────


MotionQcReadinessStatus = Literal["ready", "warning", "blocked", "unknown"]


class MotionQcInputCandidate(BaseModel):
    subject_id: str | None = None
    session_id: str | None = None
    bold_path: str
    relative_path: str | None = None
    has_sidecar: bool = False
    has_motion_params: bool = False
    motion_param_paths: list[str] = Field(default_factory=list)
    has_fd_column: bool = False
    fd_source_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class MotionQcReadinessResponse(BaseModel):
    ok: bool
    project_id: str
    status: MotionQcReadinessStatus
    checked_at: str
    candidate_count: int = 0
    candidates: list[MotionQcInputCandidate] = Field(default_factory=list)
    missing_motion_param_count: int = 0
    fd_available_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── BOLD Reference Readiness ────────────────────────────────────────────────


BoldReferenceStatus = Literal["ready", "warning", "blocked", "unknown"]
BoldReferenceStrategy = Literal["middle_volume", "single_volume", "manual_required"]


class BoldReferenceCandidate(BaseModel):
    subject_id: str | None = None
    session_id: str | None = None
    bold_path: str
    relative_path: str | None = None
    dimensions: list[int] = Field(default_factory=list)
    voxel_spacing: list[float] = Field(default_factory=list)
    volume_count: int | None = None
    is_4d: bool = False
    has_sidecar: bool = False
    repetition_time: float | None = None
    task_name: str | None = None
    has_slice_timing: bool = False
    phase_encoding_direction: str | None = None
    reference_strategy: BoldReferenceStrategy = "manual_required"
    warnings: list[str] = Field(default_factory=list)


class BoldReferenceReadinessResponse(BaseModel):
    ok: bool
    project_id: str
    status: BoldReferenceStatus
    checked_at: str
    candidate_count: int = 0
    ready_count: int = 0
    warning_count: int = 0
    blocked_count: int = 0
    candidates: list[BoldReferenceCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── rs-fMRI QC Planning Report ──────────────────────────────────────────────


class RsfmriQcPlanningReportArtifact(BaseModel):
    kind: Literal["json", "markdown"]
    path: str
    exists: bool
    size_bytes: int | None = None


class RsfmriQcPlanningReportResponse(BaseModel):
    ok: bool
    project_id: str
    status: Literal["ready", "warning", "blocked", "unknown"]
    generated_at: str
    report_dir: str
    json_path: str
    markdown_path: str
    artifacts: list[RsfmriQcPlanningReportArtifact] = Field(default_factory=list)
    bold_reference_status: str
    motion_qc_status: str
    motion_metrics_status: str | None = None
    motion_metrics_parsed_count: int = 0
    motion_metrics_fd_available_count: int = 0
    motion_metrics_artifacts: list[RsfmriQcPlanningReportArtifact] = Field(default_factory=list)
    bold_candidate_count: int = 0
    motion_candidate_count: int = 0
    ready_candidate_count: int = 0
    warning_count: int = 0
    blocked_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)
    report_markdown: str | None = None


# ── SPM Realign Dry-Run ─────────────────────────────────────────────────────


SpmRealignDryRunStatus = Literal["ready", "warning", "blocked", "unknown"]


class SpmRealignPredictedOutput(BaseModel):
    kind: Literal[
        "realigned_bold",
        "mean_bold",
        "motion_params",
        "stdout_log",
        "stderr_log",
        "provenance_json",
        "node_state_json",
    ]
    path: str
    exists: bool = False
    would_overwrite: bool = False
    warning: str | None = None


class SpmRealignInputPreview(BaseModel):
    subject_id: str | None = None
    session_id: str | None = None
    bold_path: str
    relative_path: str | None = None
    volume_count: int | None = None
    reference_strategy: str | None = None
    valid_for_realign: bool = False
    warnings: list[str] = Field(default_factory=list)
    predicted_outputs: list[SpmRealignPredictedOutput] = Field(default_factory=list)


class SpmRealignDryRunResponse(BaseModel):
    ok: bool
    project_id: str
    status: SpmRealignDryRunStatus
    dry_run: bool = True
    checked_at: str
    node_id: str = "spm_realign_subject"
    params: dict[str, Any] = Field(default_factory=dict)
    param_warnings: list[str] = Field(default_factory=list)
    param_errors: list[str] = Field(default_factory=list)
    input_count: int = 0
    ready_input_count: int = 0
    inputs: list[SpmRealignInputPreview] = Field(default_factory=list)
    output_root_preview: str | None = None
    environment_status: str | None = None
    approval_required: bool = True
    audit_required: bool = True
    execution_enabled: bool = False
    safe_allowlist_enabled: bool = False
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── SPM Realign Wrapper Skeleton ────────────────────────────────────────────


class SpmRealignProvenancePreview(BaseModel):
    command_template_id: str
    node_id: str = "spm_realign_subject"
    dry_run_only: bool = True
    project_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    input_count: int = 0
    predicted_output_count: int = 0
    environment_status: str | None = None
    approval_required: bool = True
    audit_required: bool = True
    execution_enabled: bool = False
    safe_allowlist_enabled: bool = False
    warnings: list[str] = Field(default_factory=list)


class SpmRealignWrapperSkeletonResponse(BaseModel):
    ok: bool
    project_id: str
    status: Literal["ready", "warning", "blocked", "unknown"]
    generated_at: str
    node_id: str = "spm_realign_subject"
    command_template_id: str
    dry_run: SpmRealignDryRunResponse | None = None
    matlab_batch_preview: str
    provenance_preview: SpmRealignProvenancePreview
    output_manifests: list[SpmRealignOutputManifest] = Field(default_factory=list)
    manifest_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── SPM Realign Execution Contract Schemas (future, non-executing) ──────────

SpmRealignExecutionMode = Literal["disabled", "dry_run_only", "execute"]
SpmRealignExecutionStatus = Literal[
    "not_started", "blocked", "submitted", "running",
    "succeeded", "failed", "cancelled", "timeout", "partial",
]
SpmRealignOutputKind = Literal[
    "realigned_bold", "mean_bold", "motion_params",
    "stdout_log", "stderr_log", "provenance_json",
    "node_state_json", "batch_file",
]


class SpmRealignExecutionRequest(BaseModel):
    """Future execution request.  Default execution_mode is 'disabled'."""
    model_config = {"extra": "forbid"}

    project_id: str
    reviewed_plan_id: str
    run_id: str | None = None
    node_id: str = "spm_realign_subject"
    subject_scope: list[str] = Field(default_factory=list)
    session_scope: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    approval: dict[str, Any] = Field(default_factory=dict)
    dry_run_manifest_id: str | None = None
    command_template_id: str = "spm12_realign_estwrite_v1"
    execution_mode: SpmRealignExecutionMode = "disabled"
    overwrite_policy: Literal[
        "fail_if_exists", "require_explicit_overwrite_approval"
    ] = "fail_if_exists"


class SpmRealignOutputManifestItem(BaseModel):
    kind: SpmRealignOutputKind
    path: str
    relative_path: str | None = None
    exists: bool = False
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    modified_at: str | None = None
    required: bool = True
    verified: bool = False
    warnings: list[str] = Field(default_factory=list)


class SpmRealignOutputManifest(BaseModel):
    project_id: str
    run_id: str
    node_id: str = "spm_realign_subject"
    subject_id: str | None = None
    session_id: str | None = None
    output_root: str
    items: list[SpmRealignOutputManifestItem] = Field(default_factory=list)
    missing_required_count: int = 0
    verified_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SpmRealignExecutionProvenance(BaseModel):
    project_id: str
    reviewed_plan_id: str
    run_id: str
    node_id: str = "spm_realign_subject"
    subject_id: str | None = None
    session_id: str | None = None
    command_template_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    input_paths: list[str] = Field(default_factory=list)
    input_checksums: dict[str, str] = Field(default_factory=dict)
    predicted_output_paths: list[str] = Field(default_factory=list)
    actual_output_paths: list[str] = Field(default_factory=list)
    matlab_version: str | None = None
    spm_version: str | None = None
    platform: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    batch_file_path: str | None = None
    dry_run_manifest_id: str | None = None
    approval_context: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SpmRealignFailureRecord(BaseModel):
    code: str
    message: str
    stage: Literal[
        "preflight", "approval", "audit", "environment",
        "batch_generation", "execution", "output_verification",
        "provenance", "artifact_discovery",
    ]
    subject_id: str | None = None
    session_id: str | None = None
    retryable: bool = False
    next_action: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SpmRealignExecutionResult(BaseModel):
    ok: bool
    project_id: str
    reviewed_plan_id: str
    run_id: str
    node_id: str = "spm_realign_subject"
    status: SpmRealignExecutionStatus
    execution_mode: SpmRealignExecutionMode = "disabled"
    submitted: bool = False
    executor_called: bool = False
    command_template_id: str = "spm12_realign_estwrite_v1"
    output_manifests: list[SpmRealignOutputManifest] = Field(default_factory=list)
    provenance: list[SpmRealignExecutionProvenance] = Field(default_factory=list)
    failures: list[SpmRealignFailureRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── Motion Metrics Draft ────────────────────────────────────────────────────


MotionMetricsStatus = Literal["ready", "warning", "blocked", "unknown"]


class MotionMetricsSubjectSummary(BaseModel):
    subject_id: str | None = None
    session_id: str | None = None
    bold_path: str | None = None
    source_path: str
    source_type: Literal["spm_rp_txt", "confounds_tsv", "unknown"]
    parsed: bool = False
    row_count: int = 0
    has_fd: bool = False
    volume_count_from_motion_rows: int | None = None
    max_abs_translation_mm: float | None = None
    mean_abs_translation_mm: float | None = None
    max_abs_rotation_rad: float | None = None
    mean_abs_rotation_rad: float | None = None
    fd_mean: float | None = None
    fd_max: float | None = None
    fd_over_0_2_count: int | None = None
    fd_over_0_5_count: int | None = None
    fd_over_0_2_fraction: float | None = None
    fd_over_0_5_fraction: float | None = None
    qc_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MotionMetricsDraftArtifact(BaseModel):
    kind: Literal["json", "markdown"]
    path: str
    exists: bool
    size_bytes: int | None = None


class MotionMetricsDraftResponse(BaseModel):
    ok: bool
    project_id: str
    status: MotionMetricsStatus
    generated_at: str
    report_dir: str
    json_path: str
    markdown_path: str
    artifacts: list[MotionMetricsDraftArtifact] = Field(default_factory=list)
    candidate_count: int = 0
    parsed_count: int = 0
    fd_available_count: int = 0
    summaries: list[MotionMetricsSubjectSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)
    report_markdown: str | None = None


# ── NIfTI QC Snapshot ──────────────────────────────────────────────────────

NiftiQcStatus = Literal["ready", "warning", "blocked", "unknown"]


class NiftiImageQcRecord(BaseModel):
    image_id: str
    path: str
    relative_path: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    modality: str | None = None
    suffix: str | None = None
    exists: bool = False
    readable: bool = False
    dimensions: list[int] = Field(default_factory=list)
    ndim: int | None = None
    volume_count: int | None = None
    voxel_spacing: list[float] = Field(default_factory=list)
    dtype: str | None = None
    orientation: str | None = None
    affine_determinant: float | None = None
    intensity_min: float | None = None
    intensity_max: float | None = None
    intensity_mean: float | None = None
    intensity_std: float | None = None
    zero_fraction: float | None = None
    nan_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class NiftiQcSnapshotResponse(BaseModel):
    ok: bool
    project_id: str
    status: NiftiQcStatus
    checked_at: str
    image_count: int = 0
    readable_count: int = 0
    unreadable_count: int = 0
    four_d_count: int = 0
    warning_count: int = 0
    images: list[NiftiImageQcRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── NIfTI Slice Thumbnail ──────────────────────────────────────────────────

NiftiThumbnailView = Literal["axial", "coronal", "sagittal"]


class NiftiSliceThumbnail(BaseModel):
    view: NiftiThumbnailView
    width: int
    height: int
    slice_index: int
    volume_index: int | None = None
    png_base64: str
    intensity_min: float | None = None
    intensity_max: float | None = None
    warnings: list[str] = Field(default_factory=list)


class NiftiThumbnailResponse(BaseModel):
    ok: bool
    project_id: str
    image_id: str
    path: str
    dimensions: list[int] = Field(default_factory=list)
    volume_count: int | None = None
    selected_volume_index: int | None = None
    thumbnails: list[NiftiSliceThumbnail] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


# ── QC Dashboard Report ────────────────────────────────────────────────────

QcDashboardModuleStatus = Literal["ready", "warning", "blocked", "unknown", "not_run"]


class QcDashboardModuleSummary(BaseModel):
    module_id: str
    name: str
    status: QcDashboardModuleStatus
    ok: bool = True
    score: int | None = None
    summary: str
    key_metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class QcDashboardReportArtifact(BaseModel):
    kind: Literal["json", "markdown"]
    path: str
    exists: bool
    size_bytes: int | None = None


class QcDashboardReportResponse(BaseModel):
    ok: bool
    project_id: str
    status: QcDashboardModuleStatus
    generated_at: str
    report_dir: str
    json_path: str
    markdown_path: str
    artifacts: list[QcDashboardReportArtifact] = Field(default_factory=list)
    modules: list[QcDashboardModuleSummary] = Field(default_factory=list)
    ready_count: int = 0
    warning_count: int = 0
    blocked_count: int = 0
    unknown_count: int = 0
    overall_warnings: list[str] = Field(default_factory=list)
    overall_errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)
    report_markdown: str | None = None
    cache: QcDashboardCacheSummary = Field(default_factory=lambda: QcDashboardCacheSummary(mode="off", hit=False))  # noqa: F821


# ── QC Dashboard Cache Metadata Schemas (future, non-caching) ──────────────


class RawdataFingerprint(BaseModel):
    ok: bool
    roots: list[str] = Field(default_factory=list)
    exists_count: int = 0
    missing_roots: list[str] = Field(default_factory=list)
    file_count: int = 0
    total_size_bytes: int = 0
    newest_mtime: float | None = None
    newest_mtime_iso: str | None = None
    relative_path_hash: str | None = None
    fingerprint: str | None = None
    truncated: bool = False
    max_files: int = 20000
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


QcDashboardCacheMode = Literal["prefer", "refresh", "off"]
QcDashboardCacheStatus = Literal["hit", "miss", "stale", "disabled", "error"]


class QcDashboardModuleCacheRecord(BaseModel):
    module_id: str
    status: QcDashboardCacheStatus = "miss"
    cache_key: str | None = None
    fingerprint: str | None = None
    module_version: str | None = None
    generated_at: str | None = None
    artifact_path: str | None = None
    hit: bool = False
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class QcDashboardCacheSummary(BaseModel):
    mode: QcDashboardCacheMode = "off"
    hit: bool = False
    fingerprint: str | None = None
    module_hits: dict[str, bool] = Field(default_factory=dict)
    module_records: list[QcDashboardModuleCacheRecord] = Field(default_factory=list)
    cache_warnings: list[str] = Field(default_factory=list)
    cache_errors: list[str] = Field(default_factory=list)


# ── QC Dashboard Fingerprint Debug ──────────────────────────────────────────


class QcDashboardFingerprintResponse(BaseModel):
    ok: bool
    project_id: str
    fingerprint: RawdataFingerprint
    roots: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)
