"""Tests for the native preprocessing contract schemas."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.backend.app.schemas.native_preproc import (
    NATIVE_PREPROC_ARTIFACT_TYPES,
    NATIVE_PREPROC_CAPABILITY_LEVEL_VALUES,
    NATIVE_PREPROC_REUSE_STRATEGIES,
    NATIVE_PREPROC_STAGE_IDS,
    NATIVE_PREPROC_STAGE_STATUS_VALUES,
    NativePreprocArtifactRef,
    NativePreprocStageResult,
    NativePreprocStageSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_stage_ids_match_native_preproc_contract_order() -> None:
    assert NATIVE_PREPROC_STAGE_IDS == (
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


def test_status_and_capability_values_preserve_truth_levels() -> None:
    assert NATIVE_PREPROC_STAGE_STATUS_VALUES == (
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
    for value in ("unavailable", "scaffolded", "metadata_only", "computed", "validated"):
        assert value in NATIVE_PREPROC_CAPABILITY_LEVEL_VALUES
    for value in ("numerically_implemented", "simplified", "affine_only", "reference_validated"):
        assert value in NATIVE_PREPROC_CAPABILITY_LEVEL_VALUES


def test_artifact_types_cover_planned_numeric_outputs() -> None:
    required = {
        "bold_4d",
        "t1w",
        "mean_functional",
        "motion_parameters",
        "fd_timeseries",
        "confound_matrix",
        "transform_matrix",
        "normalized_bold",
        "smoothed_bold",
        "residual_bold",
        "filtered_bold",
        "alff_map",
        "falff_map",
        "reho_map",
        "atlas",
        "atlas_resampled",
        "roi_timeseries",
        "fc_matrix",
        "fisher_z_matrix",
        "qc_json",
        "qc_md",
        "manifest",
        "provenance",
    }
    assert required.issubset(set(NATIVE_PREPROC_ARTIFACT_TYPES))


def test_reuse_strategy_values_are_closed() -> None:
    assert NATIVE_PREPROC_REUSE_STRATEGIES == (
        "clean_room_rewrite",
        "behavior_reference_only",
        "golden_output_reference",
        "not_needed",
        "defer",
    )


def test_stage_result_contains_required_contract_fields() -> None:
    required_fields = {
        "stage_id",
        "backend",
        "input_artifacts",
        "output_artifacts",
        "parameters",
        "status",
        "capability_level",
        "validation_status",
        "warnings",
        "errors",
        "provenance",
        "qc",
    }
    assert required_fields.issubset(set(NativePreprocStageResult.model_fields))


def test_stage_result_accepts_computed_artifact_contract() -> None:
    result = NativePreprocStageResult(
        stage_id="functional_connectivity",
        backend="native_python",
        input_artifacts=[NativePreprocArtifactRef(artifact_type="filtered_bold")],
        output_artifacts=[NativePreprocArtifactRef(artifact_type="fc_matrix")],
        status="succeeded",
        capability_level="computed",
        validation_status="synthetic_tested",
    )
    assert result.output_artifacts[0].artifact_type == "fc_matrix"


def test_stage_result_rejects_metadata_only_as_computed() -> None:
    with pytest.raises(ValidationError, match="metadata_only status cannot claim"):
        NativePreprocStageResult(
            stage_id="alff",
            backend="native_python",
            status="metadata_only",
            capability_level="computed",
            validation_status="not_validated",
        )


def test_stage_result_rejects_succeeded_scaffold() -> None:
    with pytest.raises(ValidationError, match="succeeded status requires"):
        NativePreprocStageResult(
            stage_id="slice_timing",
            backend="native_python",
            status="succeeded",
            capability_level="scaffolded",
            validation_status="planned",
        )


def test_reference_validation_requires_validated_capability() -> None:
    with pytest.raises(ValidationError, match="reference validation status"):
        NativePreprocStageResult(
            stage_id="realignment",
            backend="native_python",
            status="reference_validated",
            capability_level="computed",
            validation_status="reference_validated",
        )


def test_stage_spec_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        NativePreprocStageSpec(
            stage_id="slice_timing",
            unsupported=True,  # type: ignore[call-arg]
        )


def test_schema_module_has_no_execution_imports() -> None:
    import src.backend.app.schemas.native_preproc as mod

    path = Path(mod.__file__ or "")
    source = path.read_text(encoding="utf-8")
    forbidden = (
        "import subprocess",
        "from subprocess",
        "os.system",
        "matlab_runner",
        "spm_runner",
        "dpabi_runner",
        "node_registry",
        "pipeline_executor",
    )
    for token in forbidden:
        assert token not in source


def test_stage_matrix_document_covers_schema_stage_ids() -> None:
    text = (REPO_ROOT / "docs" / "预处理与科学计算" / "原生预处理" / "阶段矩阵.md").read_text(
        encoding="utf-8"
    )
    for stage_id in NATIVE_PREPROC_STAGE_IDS:
        assert f"`{stage_id}`" in text


def test_function_coverage_matrix_uses_closed_reuse_strategies() -> None:
    text = (REPO_ROOT / "docs" / "预处理与科学计算" / "原生预处理" / "功能覆盖矩阵.md").read_text(
        encoding="utf-8"
    )
    strategies = set(NATIVE_PREPROC_REUSE_STRATEGIES)
    for line in text.splitlines():
        if not line.startswith("| ") or "Reuse strategy" in line or line.startswith("| ------"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 8:
            continue
        assert columns[5].strip("`") in strategies


def test_contract_document_keeps_native_runtime_prohibitions() -> None:
    text = (REPO_ROOT / "docs" / "预处理与科学计算" / "原生预处理" / "原生预处理契约.md").read_text(
        encoding="utf-8"
    )
    for token in ("matlab -batch", "matlab -r", "spm_jobman", "DPARSFA_run", "DPABI_run"):
        assert token in text
