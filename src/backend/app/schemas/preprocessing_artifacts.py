"""Preprocessing artifact registry schemas.

These models describe persisted references to preprocessing inputs and stage
outputs. They are metadata contracts only; they do not execute preprocessing or
read image payloads beyond optional file checksums.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

PREPROCESSING_ARTIFACT_TYPES: tuple[str, ...] = (
    "acpc_t1w",
    "acpc_landmarks",
    "converted_bold",
    "converted_t1w",
    "sidecar_json",
    "dummy_removed_bold",
    "slice_timing_corrected_bold",
    "realigned_bold",
    "mean_bold",
    "motion_parameters",
    "fd_timeseries",
    "motion_qc_summary",
    "coregistered_t1w",
    "segmentation_maps",
    "normalized_bold",
    "smoothed_bold",
    "confounds_tsv",
    "denoised_bold",
    "detrended_bold",
    "filtered_bold",
    "alff_map",
    "falff_map",
    "reho_map",
    "atlas",
    "roi_labels",
    "roi_timeseries",
    "fc_matrix",
    "fisher_z_matrix",
    "qc_json",
    "qc_markdown",
    "stage_manifest",
    "pipeline_report",
    "provenance_json",
    "input_inventory",
)


class BidsEntitySet(BaseModel):
    subject_id: str = ""
    session_id: str = ""
    task: str = ""
    run_id: str = ""
    acquisition: str = ""
    direction: str = ""
    datatype: str = ""
    suffix: str = ""
    extension: str = ""
    raw_entities: dict[str, str] = Field(default_factory=dict)


class PreprocessingArtifactRef(BaseModel):
    artifact_id: str = ""
    artifact_type: str = ""
    stage_id: str = ""
    subject_id: str = ""
    session_id: str = ""
    run_id: str = ""
    path: str = ""
    path_kind: str = "project_relative"
    shape: list[int] = Field(default_factory=list)
    dtype: str = ""
    space: str = ""
    desc: str = ""
    suffix: str = ""
    source_artifact_ids: list[str] = Field(default_factory=list)
    checksum: str = ""
    created_at: str = ""
    backend: str = ""
    provenance_path: str = ""
    qc_path: str = ""
    bids_entities: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreprocessingInputInventory(BaseModel):
    source_kind: str = "converted_bids"
    conversion_run_id: str = ""
    input_root: str = ""
    input_root_path_kind: str = "project_relative"
    subjects: list[str] = Field(default_factory=list)
    sessions: list[str] = Field(default_factory=list)
    bold_count: int = 0
    t1w_count: int = 0
    nifti_count: int = 0
    sidecar_count: int = 0
    missing_t1w_subjects: list[str] = Field(default_factory=list)
    missing_bold_subjects: list[str] = Field(default_factory=list)
    missing_sidecar_pairings: list[dict[str, str]] = Field(default_factory=list)
    bids_entities: list[dict[str, Any]] = Field(default_factory=list)
    artifact_ids_by_type: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PreprocessingArtifactRegistry(BaseModel):
    registry_schema_version: str = "1"
    project_id: str = ""
    preprocessing_run_id: str = ""
    source_kind: str = "converted_bids"
    conversion_run_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    registry_root: str = ""
    registry_root_path_kind: str = "project_relative"
    input_inventory: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[PreprocessingArtifactRef] = Field(default_factory=list)
    lineage: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool] = Field(default_factory=dict)


class PreprocessingArtifactRegistryWriteResult(BaseModel):
    ok: bool = False
    status: str = "blocked"
    registry_path: str = ""
    registry_root: str = ""
    artifact_count: int = 0
    artifacts_by_type: dict[str, int] = Field(default_factory=dict)
    inventory: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)


__all__ = [
    "BidsEntitySet",
    "PREPROCESSING_ARTIFACT_TYPES",
    "PreprocessingArtifactRef",
    "PreprocessingArtifactRegistry",
    "PreprocessingArtifactRegistryWriteResult",
    "PreprocessingInputInventory",
]
