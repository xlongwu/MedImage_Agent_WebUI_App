from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentExecuteRequest(BaseModel):
    agent_run_id: str = Field(default="agent_run_001")
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_subject_preprocess.yaml")
    approved: bool = Field(default=False)


class FileReadResponse(BaseModel):
    ok: bool
    path: str
    relative_path: str
    content: str
    size_bytes: int


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str


class RetryDryRunRequest(BaseModel):
    run_id: str = Field(default="run_subject_preprocess_001")
    retry_run_id: str | None = Field(default=None)


class RetryExecuteRequest(BaseModel):
    run_id: str = Field(default="run_subject_preprocess_001")
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    retry_run_id: str | None = Field(default=None)
    approved: bool = Field(default=False)


class SchedulerPlanRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_subject_preprocess_parallel.yaml")


class PlannerDraftRequest(BaseModel):
    disease_type: str = Field(default="unspecified")
    modality: str = Field(default="rs-fMRI")
    downstream_task: str = Field(default="standard preprocessing")
    available_data: list[str] = Field(default_factory=lambda: ["T1w", "BOLD"])
    constraints: list[str] = Field(default_factory=list)
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str | None = Field(default=None)
    plan_id: str | None = Field(default=None)


class PlannerValidateRequest(BaseModel):
    plan_id: str | None = Field(default=None)
    draft: dict | None = Field(default=None)
    disease_type: str = Field(default="unspecified")
    modality: str = Field(default="rs-fMRI")
    downstream_task: str = Field(default="standard preprocessing")
    available_data: list[str] = Field(default_factory=lambda: ["T1w", "BOLD"])
    constraints: list[str] = Field(default_factory=list)
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str | None = Field(default=None)


class PlannerExecuteRequest(BaseModel):
    plan_id: str | None = Field(default=None)
    draft: dict | None = Field(default=None)
    disease_type: str = Field(default="unspecified")
    modality: str = Field(default="rs-fMRI")
    downstream_task: str = Field(default="standard preprocessing")
    available_data: list[str] = Field(default_factory=lambda: ["T1w", "BOLD"])
    constraints: list[str] = Field(default_factory=list)
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str | None = Field(default=None)
    approved: bool = Field(default=False)


class DesktopConfigSaveRequest(BaseModel):
    project_dir: str | None = Field(default=None)
    python_path: str | None = Field(default=None)
    matlab_command: str | None = Field(default=None)
    spm_dir: str | None = Field(default=None)
    dpabi_dir: str | None = Field(default=None)
    gpu_mode: str | None = Field(default=None)
    llm: dict = Field(default_factory=dict)


class ExternalSmokeRunRequest(BaseModel):
    target: str = Field(default="all")
    mode: str = Field(default="manual_package")
    config_path: str = Field(default="examples/project_config.yaml")
    approved: bool = Field(default=False)
    approved_by: str = Field(default="local-user")
    dpabi_function: str = Field(default="y_Smooth")


class GpuBenchmarkRequest(BaseModel):
    subject_id: str = Field(default="sub-001")
    input_nii: str = Field(default="./derivatives/spm_smooth/sub-001/func/sub-001_task-rest_bold_smooth.nii")
    derivatives_dir: str = Field(default="./derivatives")
    tr: float = Field(default=2.0)
    freq_band: list[float] = Field(default=[0.01, 0.08])
    prefer_gpu: bool = Field(default=True)
    require_gpu: bool = Field(default=False)
    benchmark_compare_cpu_gpu: bool = Field(default=True)


class DpabiCapabilityRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")


class DpabiPreflightRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    dataset_index: str = Field(default="./work/dataset_index/dataset_index.json")
    capabilities_path: str = Field(default="./work/dpabi/dpabi_capabilities.json")
    manifest_path: str = Field(default="./work/dpabi/dpabi_input_manifest.json")
    wrapper_config_template_path: str = Field(default="./work/dpabi/dpabi_wrapper_config_template.yaml")


class DpabiRunPlanRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    capabilities_path: str = Field(default="./work/dpabi/dpabi_capabilities.json")
    manifest_path: str = Field(default="./work/dpabi/dpabi_input_manifest.json")
    preflight_path: str = Field(default="./work/dpabi/dpabi_preflight_report.json")
    params_path: str = Field(default="./work/dpabi/dpabi_params_review.yaml")


class DpabiSandboxSmokeRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    approved: bool = Field(default=False)
    approved_by: str = Field(default="local-user")


class DpabiSignatureProbeRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")


class DpabiSingleFunctionRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    function_name: str = Field(default="y_Smooth")
    approved: bool = Field(default=False)
    approved_by: str = Field(default="local-user")


class DpabiSubjectSmoothRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    subject_id: str = Field(default="sub-01")
    input_bold: str = Field(default="examples/synthetic_bids/rawdata/sub-01/func/sub-01_task-rest_bold.nii.gz")
    function_name: str = Field(default="y_Smooth")
    fwhm: list[float] = Field(default=[4.0, 4.0, 4.0])
    approved: bool = Field(default=False)


class DpabiSubjectWrapperReportRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")


class DpabiWrapperValidationRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    signatures_path: str = Field(default="./work/dpabi/dpabi_function_signatures.json")
    contracts_path: str = Field(default="./work/dpabi/dpabi_wrapper_contracts.json")
    sandbox_result_path: str = Field(default="./work/dpabi/single_function_sandbox/dpabi_single_function_result.json")
    subject_wrapper_summary_path: str = Field(default="./reports/dpabi/dpabi_subject_wrapper_summary.json")


class DpabiTemplateInstantiateRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    template_id: str = Field(default="dpabi_y_smooth_subject_wrapper_template")
    instance_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)
    function_name: str | None = Field(default=None)
    fwhm: list[float] | None = Field(default=None)
    subjects: list[str] | None = Field(default=None)


class DpabiTemplateExecuteRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    work_dir: str = Field(default="./work")
    log_dir: str = Field(default="./logs")
    instance_id: str = Field(default="")
    approved: bool = Field(default=False)
    approved_by: str = Field(default="local-user")


class DpabiTemplateWizardRequest(BaseModel):
    template_id: str = Field(default="dpabi_y_smooth_subject_wrapper_template")
    instance_id: str | None = Field(default="instance_dpabi_y_smooth_001")
    run_id: str | None = Field(default=None)
    function_name: str = Field(default="y_Smooth")
    fwhm: list[float] = Field(default=[4, 4, 4])
    subjects: list[str] = Field(default=["sub-001", "sub-002"])
    scheduler: dict = Field(
        default={
            "mode": "local_parallel",
            "max_workers": 2,
            "matlab_max_workers": 1,
        }
    )


class ExperimentTrackingRequest(BaseModel):
    experiment_id: str = Field(default="experiment_001")
    name: str = Field(default="Experiment 001")
    run_ids: list[str] = Field(default=[])
    tags: list[str] = Field(default=[])
    notes: str = Field(default="")


class ExperimentCompareRequest(BaseModel):
    experiment_id: str = Field(default="comparison_001")
    run_ids: list[str] = Field(default=[])


class ArtifactPreviewRequest(BaseModel):
    path: str = Field(default="")


class BundleCreateRequest(BaseModel):
    bundle_id: str | None = Field(default=None)
    include_logs: bool = Field(default=True)
    include_reports: bool = Field(default=True)
    include_artifact_index: bool = Field(default=True)
    max_file_size_bytes: int = Field(default=2_000_000)


class RsfmriSpmRealignMotionQcRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_spm_realign_motion_qc.yaml")
    approved: bool = Field(default=False)


class RsfmriSpmSliceTimingRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_spm_slice_timing.yaml")
    approved: bool = Field(default=False)


class RsfmriStRealignMotionQcRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_st_realign_motion_qc.yaml")
    approved: bool = Field(default=False)


class RsfmriCoregistrationQcRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_coregistration_qc.yaml")
    approved: bool = Field(default=False)


class RsfmriSegmentationTissueQcRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_segmentation_tissue_qc.yaml")
    approved: bool = Field(default=False)


class RsfmriNormalizationQcRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_normalization_qc.yaml")
    approved: bool = Field(default=False)


class RsfmriSmoothingQcRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_smoothing_qc.yaml")
    approved: bool = Field(default=False)


class RsfmriNuisanceRegressionRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_nuisance_regression.yaml")
    approved: bool = Field(default=False)


class RsfmriTemporalFilteringRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_temporal_filtering.yaml")
    approved: bool = Field(default=False)


class RsfmriAlffFalffRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_alff_falff.yaml")
    approved: bool = Field(default=False)


class RsfmriRehoRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_reho.yaml")
    approved: bool = Field(default=False)


class RsfmriFunctionalConnectivityRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_functional_connectivity.yaml")
    approved: bool = Field(default=False)


class RsfmriGroupSummaryRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_group_summary.yaml")


class RsfmriReportExportRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_report_exporter.yaml")


class RsfmriReportValidationRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_report_validator.yaml")


class ReleaseReadinessRequest(BaseModel):
    project_config_path: str = Field(default="examples/project_config_dataset.yaml")
    pipeline_path: str = Field(default="examples/pipeline_rsfmri_release_readiness.yaml")


class ProjectCreateRequest(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=128)
    rawdata_dir: str = Field(..., min_length=1)
    project_dir: str | None = Field(default=None)
    copy_mode: Literal["reference"] = Field(default="reference")
    run_inspection: bool = Field(default=True)
    overwrite: bool = Field(default=False)


class ProjectCreateResponse(BaseModel):
    ok: bool
    project_id: str
    project_name: str
    project_dir: str
    rawdata_dir: str
    project_config_path: str
    dataset_index_path: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
