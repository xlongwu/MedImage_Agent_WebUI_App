"""Native preprocessing contract schemas.

These models are schema-only. They define the contract for the future
Python-native rs-fMRI preprocessing backend and intentionally do not import
runtime runners, subprocess helpers, MATLAB/SPM/DPABI wrappers, or scientific
kernels.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NativePreprocStageId = Literal[
    "input_validation",
    "auto_acpc_align",
    "dicom_to_nifti",
    "bids_sidecar_validation",
    "dummy_scan_removal",
    "slice_timing",
    "realignment",
    "motion_qc",
    "coregistration",
    "segmentation",
    "normalization",
    "smoothing",
    "nuisance_regression",
    "detrending",
    "temporal_filtering",
    "alff",
    "falff",
    "reho",
    "atlas_resampling",
    "roi_timeseries",
    "functional_connectivity",
    "subject_qc",
    "group_summary",
    "validation_report",
    "final_report",
]

NativePreprocStageStatus = Literal[
    "not_started",
    "planned",
    "running",
    "succeeded",
    "partial",
    "warning",
    "failed",
    "blocked",
    "metadata_only",
    "simplified",
    "reference_validated",
]

NativePreprocCapabilityLevel = Literal[
    "unavailable",
    "scaffolded",
    "metadata_only",
    "computed",
    "numerically_implemented",
    "simplified",
    "affine_only",
    "validated",
    "reference_validated",
]

NativePreprocValidationStatus = Literal[
    "not_validated",
    "planned",
    "synthetic_tested",
    "golden_reference_pending",
    "reference_validated",
    "failed",
    "blocked",
    "not_applicable",
]

NativePreprocBackend = Literal[
    "native_python",
    "python",
    "gpu",
    "external_reference",
    "matlab_spm",
    "matlab_dpabi",
    "unknown",
]

NativePreprocArtifactType = Literal[
    "acpc_t1w",
    "acpc_landmarks",
    "bold_4d",
    "t1w",
    "sidecar_json",
    "mean_functional",
    "motion_parameters",
    "fd_timeseries",
    "confound_matrix",
    "transform_matrix",
    "deformation_field",
    "brain_mask",
    "gm_map",
    "wm_map",
    "csf_map",
    "normalized_bold",
    "smoothed_bold",
    "residual_bold",
    "detrended_bold",
    "filtered_bold",
    "alff_map",
    "falff_map",
    "reho_map",
    "atlas",
    "atlas_resampled",
    "roi_labels",
    "roi_timeseries",
    "fc_matrix",
    "fisher_z_matrix",
    "qc_json",
    "qc_md",
    "manifest",
    "provenance",
    "group_summary",
    "validation_report",
    "final_report",
]

NativePreprocReuseStrategy = Literal[
    "clean_room_rewrite",
    "behavior_reference_only",
    "golden_output_reference",
    "not_needed",
    "defer",
]


NATIVE_PREPROC_STAGE_IDS: tuple[NativePreprocStageId, ...] = (
    "input_validation",
    "auto_acpc_align",
    "dicom_to_nifti",
    "bids_sidecar_validation",
    "dummy_scan_removal",
    "slice_timing",
    "realignment",
    "motion_qc",
    "coregistration",
    "segmentation",
    "normalization",
    "smoothing",
    "nuisance_regression",
    "detrending",
    "temporal_filtering",
    "alff",
    "falff",
    "reho",
    "atlas_resampling",
    "roi_timeseries",
    "functional_connectivity",
    "subject_qc",
    "group_summary",
    "validation_report",
    "final_report",
)

NATIVE_PREPROC_STAGE_STATUS_VALUES: tuple[NativePreprocStageStatus, ...] = (
    "not_started",
    "planned",
    "running",
    "succeeded",
    "partial",
    "warning",
    "failed",
    "blocked",
    "metadata_only",
    "simplified",
    "reference_validated",
)

NATIVE_PREPROC_CAPABILITY_LEVEL_VALUES: tuple[NativePreprocCapabilityLevel, ...] = (
    "unavailable",
    "scaffolded",
    "metadata_only",
    "computed",
    "numerically_implemented",
    "simplified",
    "affine_only",
    "validated",
    "reference_validated",
)

NATIVE_PREPROC_ARTIFACT_TYPES: tuple[NativePreprocArtifactType, ...] = (
    "acpc_t1w",
    "acpc_landmarks",
    "bold_4d",
    "t1w",
    "sidecar_json",
    "mean_functional",
    "motion_parameters",
    "fd_timeseries",
    "confound_matrix",
    "transform_matrix",
    "deformation_field",
    "brain_mask",
    "gm_map",
    "wm_map",
    "csf_map",
    "normalized_bold",
    "smoothed_bold",
    "residual_bold",
    "detrended_bold",
    "filtered_bold",
    "alff_map",
    "falff_map",
    "reho_map",
    "atlas",
    "atlas_resampled",
    "roi_labels",
    "roi_timeseries",
    "fc_matrix",
    "fisher_z_matrix",
    "qc_json",
    "qc_md",
    "manifest",
    "provenance",
    "group_summary",
    "validation_report",
    "final_report",
)

NATIVE_PREPROC_REUSE_STRATEGIES: tuple[NativePreprocReuseStrategy, ...] = (
    "clean_room_rewrite",
    "behavior_reference_only",
    "golden_output_reference",
    "not_needed",
    "defer",
)


class NativePreprocArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = ""
    artifact_type: NativePreprocArtifactType
    path: str = ""
    path_kind: Literal[
        "project_relative",
        "run_relative",
        "repo_relative",
        "absolute_local",
        "external_reference",
        "not_persisted",
    ] = "project_relative"
    shape: list[int] = Field(default_factory=list)
    dtype: str = ""
    checksum: str = ""
    source_artifact_ids: list[str] = Field(default_factory=list)
    provenance_path: str = ""
    qc_path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class NativePreprocProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm_id: str = ""
    algorithm_version: str = ""
    implementation: Literal["native_python", "external_reference", "legacy_external"] = "native_python"
    input_artifact_ids: list[str] = Field(default_factory=list)
    input_checksums: dict[str, str] = Field(default_factory=dict)
    subject_id: str = ""
    session_id: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    backend: NativePreprocBackend = "native_python"
    precision: str = ""
    dtype: str = ""
    random_seed: int | None = None
    package_versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    output_checksums: dict[str, str] = Field(default_factory=dict)
    created_at: str = ""


class NativePreprocQC(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_run", "pass", "warning", "fail", "not_applicable"] = "not_run"
    metrics: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class NativePreprocStageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: NativePreprocStageId
    display_name: str = ""
    category: str = ""
    input_artifact_types: list[NativePreprocArtifactType] = Field(default_factory=list)
    output_artifact_types: list[NativePreprocArtifactType] = Field(default_factory=list)
    default_backend: NativePreprocBackend = "native_python"
    initial_capability_level: NativePreprocCapabilityLevel = "unavailable"
    validation_status: NativePreprocValidationStatus = "planned"
    depends_on: list[NativePreprocStageId] = Field(default_factory=list)
    required_for_full_pipeline: bool = True
    notes: list[str] = Field(default_factory=list)


class NativePreprocStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: NativePreprocStageId
    backend: NativePreprocBackend
    input_artifacts: list[NativePreprocArtifactRef] = Field(default_factory=list)
    output_artifacts: list[NativePreprocArtifactRef] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: NativePreprocStageStatus
    capability_level: NativePreprocCapabilityLevel
    validation_status: NativePreprocValidationStatus
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    provenance: NativePreprocProvenance = Field(default_factory=NativePreprocProvenance)
    qc: NativePreprocQC = Field(default_factory=NativePreprocQC)

    @model_validator(mode="after")
    def _check_truthfulness(self) -> NativePreprocStageResult:
        numeric_levels = {
            "computed",
            "numerically_implemented",
            "validated",
            "reference_validated",
        }
        non_numeric_levels = {"unavailable", "scaffolded", "metadata_only"}
        if self.status == "metadata_only" and self.capability_level in numeric_levels:
            raise ValueError("metadata_only status cannot claim numeric capability")
        if self.status == "succeeded" and self.capability_level in non_numeric_levels:
            raise ValueError("succeeded status requires a numeric or explicit simplified capability")
        if self.validation_status == "reference_validated" and self.capability_level not in {
            "validated",
            "reference_validated",
        }:
            raise ValueError("reference validation status requires validated capability")
        return self


__all__ = [
    "NATIVE_PREPROC_ARTIFACT_TYPES",
    "NATIVE_PREPROC_CAPABILITY_LEVEL_VALUES",
    "NATIVE_PREPROC_REUSE_STRATEGIES",
    "NATIVE_PREPROC_STAGE_IDS",
    "NATIVE_PREPROC_STAGE_STATUS_VALUES",
    "NativePreprocArtifactRef",
    "NativePreprocArtifactType",
    "NativePreprocBackend",
    "NativePreprocCapabilityLevel",
    "NativePreprocProvenance",
    "NativePreprocQC",
    "NativePreprocReuseStrategy",
    "NativePreprocStageId",
    "NativePreprocStageResult",
    "NativePreprocStageSpec",
    "NativePreprocStageStatus",
    "NativePreprocValidationStatus",
]
