"""API schemas for the native full preprocessing workflow."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

NativeFullRunStatus = Literal[
    "planned",
    "queued",
    "running",
    "cancel_requested",
    "cancelled",
    "interrupted",
    "succeeded",
    "partial",
    "blocked",
    "failed",
]


class NativeFullPreprocConfirmations(BaseModel):
    confirm_reviewed_native_execution: bool = False
    confirm_rawdata_readonly: bool = False
    confirm_no_external_tools: bool = False
    confirm_research_use_only: bool = False
    confirm_no_clinical_use: bool = False


class NativeCpuExecutionPolicy(BaseModel):
    """Resource controls for subject-level native CPU execution.

    Values supplied by a caller are deliberately bounds, not a promise that a
    run may consume those resources.  The runtime planner remains the final
    authority so a copied request cannot overcommit a different workstation.
    """

    mode: Literal["serial", "process", "auto"] = "serial"
    max_subject_workers: int | None = Field(default=None, ge=1, le=32)
    cpu_threads_per_worker: int | None = Field(default=None, ge=1, le=64)
    memory_budget_bytes: int | None = Field(default=None, ge=1)
    reserve_cpu_threads: int | None = Field(default=None, ge=0, le=64)
    adaptive_replanning: bool = True

    @model_validator(mode="after")
    def validate_resource_bounds(self) -> NativeCpuExecutionPolicy:
        if self.memory_budget_bytes is not None and self.memory_budget_bytes < 64 * 1024 * 1024:
            raise ValueError("memory_budget_bytes must reserve at least 64 MiB.")
        return self


NativeComputeBackend = Literal["cpu", "gpu", "auto"]
NativeComputeDevice = Literal["auto", "cuda:0"]
_GPU_CAPABLE_NATIVE_STAGES = frozenset({
    "alff", "falff", "temporal_filtering", "nuisance_regression", "functional_connectivity",
    "smoothing", "atlas_resampling",
})


class NativeComputePolicy(BaseModel):
    """Reviewed compute policy for native scientific stages.

    This deliberately models only reviewed choices.  It is not a pass-through
    for CUDA options, kernel names, paths, or arbitrary device identifiers.
    ``gpu`` is a require-GPU request; only ``auto`` may fall back to CPU.
    """

    backend: NativeComputeBackend = "cpu"
    device: NativeComputeDevice = "auto"
    precision: Literal["float32", "float64"] = "float32"
    gpu_memory_budget_bytes: int | None = Field(default=None, ge=1)
    max_gpu_jobs: int | None = Field(default=None, ge=1, le=32)
    chunk_size: int | None = Field(default=None, ge=1)
    allow_cpu_fallback: bool = True
    adaptive_replanning: bool = True
    stage_backends: dict[str, NativeComputeBackend] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_gpu_policy(self) -> NativeComputePolicy:
        unknown = sorted(set(self.stage_backends) - _GPU_CAPABLE_NATIVE_STAGES)
        if unknown:
            raise ValueError(f"stage_backends contains unsupported or non-GPU stage IDs: {', '.join(unknown)}.")
        if self.gpu_memory_budget_bytes is not None and self.gpu_memory_budget_bytes < 64 * 1024 * 1024:
            raise ValueError("gpu_memory_budget_bytes must reserve at least 64 MiB.")
        # A require-GPU request never silently degrades, even if a legacy
        # client sends the old fallback field as true.
        if self.backend == "gpu" and self.allow_cpu_fallback is False:
            return self
        return self


class NativeFullPreprocRequest(BaseModel):
    run_id: str = ""
    subject_id: str = ""
    session_id: str = ""
    output_dir: str = ""

    input_bold: str = ""
    input_bids_dir: str = ""
    sidecar_json: str = ""
    t1w: str = ""
    template: str = ""
    atlas: str = ""
    atlas_labels: str = ""
    conversion_run_id: str = ""
    dparsf_config: dict[str, Any] = Field(default_factory=dict)
    stage_overrides: dict[str, bool] = Field(default_factory=dict)
    cpu_policy: NativeCpuExecutionPolicy = Field(default_factory=NativeCpuExecutionPolicy)
    compute_policy: NativeComputePolicy = Field(default_factory=NativeComputePolicy)

    remove_first: int = 0
    enable_slice_timing: bool = True
    reference_time: float | None = None
    reference_slice_index: int | None = None
    reference_volume_index: int = 0
    fd_threshold_mm: float = 0.5
    head_radius_mm: float = 50.0
    fwhm_mm: float | list[float] = 6.0
    include_wm: bool = True
    include_csf: bool = True
    include_global_signal: bool = False
    polynomial_order: int = 1
    temporal_filter_type: str = "band-pass"
    low_hz: float | None = 0.01
    high_hz: float | None = 0.08
    tr: float | None = None
    filtering_method: str = "fft"
    reho_neighborhood: int = 27
    atlas_name: str = "custom"

    confirmations: NativeFullPreprocConfirmations = Field(
        default_factory=NativeFullPreprocConfirmations
    )


class NativeFullStageApiResult(BaseModel):
    stage_id: str
    display_name: str = ""
    node_id: str = ""
    status: str
    capability_level: str = ""
    validation_status: str = ""
    backend: str = "native_python"
    input_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    output_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class NativeFullPreprocResponse(BaseModel):
    ok: bool = False
    status: NativeFullRunStatus = "blocked"
    dry_run: bool = False
    project_id: str = ""
    run_id: str = ""
    run_dir: str = ""
    backend: str = "native_python"
    stage_graph: list[dict[str, Any]] = Field(default_factory=list)
    stage_results: list[NativeFullStageApiResult] = Field(default_factory=list)
    completed_stages: list[str] = Field(default_factory=list)
    blocked_stages: list[str] = Field(default_factory=list)
    failed_stages: list[str] = Field(default_factory=list)
    skipped_stages: list[str] = Field(default_factory=list)
    metadata_only_stages: list[str] = Field(default_factory=list)
    warning_stages: list[str] = Field(default_factory=list)
    artifact_count: int = 0
    manifest_path: str = ""
    validation_report_path: str = ""
    final_report_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)
    scheduler_mode: Literal["serial", "process", "auto"] = "serial"
    worker_count_requested: int | None = None
    worker_count_calculated: int = 1
    worker_count_used: int = 1
    threads_per_worker_calculated: int = 1
    resource_decision: dict[str, Any] = Field(default_factory=dict)
    subject_execution: list[dict[str, Any]] = Field(default_factory=list)
    progress_url: str = ""
    started_at: str = ""
    finished_at: str = ""
    runtime_seconds: float | None = None


class AcpcRequest(BaseModel):
    """Reviewed request for template-rigid ACPC estimation."""

    project_id: str = ""
    project_dir: str = ""
    source_t1_artifact_id: str = Field(min_length=1)
    output_root: str = ""
    template_id: Literal["spm12_avg152_t1_ras"] = "spm12_avg152_t1_ras"
    interpolation: Literal["linear", "cubic"] = "linear"


class AcpcLandmarks(BaseModel):
    estimated_ac_mm: list[float] = Field(min_length=3, max_length=3)
    estimated_pc_mm: list[float] = Field(min_length=3, max_length=3)
    msp_normal: list[float] = Field(min_length=3, max_length=3)
    coordinate_system: str = "RAS+ mm"


class AcpcQc(BaseModel):
    converged: bool = False
    cost: float | None = None
    checks: dict[str, bool] = Field(default_factory=dict)
    review_required: bool = True
    failure_code: str = ""


class AcpcResult(BaseModel):
    ok: bool = False
    status: Literal["computed", "partial", "failed", "blocked"] = "blocked"
    transform_artifact_id: str = ""
    aligned_t1_artifact_id: str = ""
    landmarks_artifact_id: str = ""
    landmarks: AcpcLandmarks | None = None
    qc: AcpcQc = Field(default_factory=AcpcQc)
    provenance: dict[str, Any] = Field(default_factory=dict)
    registry_path: str = ""
    errors: list[str] = Field(default_factory=list)


__all__ = [
    "NativeFullPreprocConfirmations",
    "AcpcLandmarks",
    "AcpcQc",
    "AcpcRequest",
    "AcpcResult",
    "NativeCpuExecutionPolicy",
    "NativeComputePolicy",
    "NativeFullPreprocRequest",
    "NativeFullPreprocResponse",
    "NativeFullRunStatus",
    "NativeFullStageApiResult",
]
