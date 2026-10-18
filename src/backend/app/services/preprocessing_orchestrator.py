"""Reviewed preprocessing orchestrator service.

The orchestrator coordinates stage status, prerequisite checks, resume/rerun
policy, and report/validation generation for full preprocessing runs. It does
not open a new unrestricted external execution path: MATLAB/SPM stages remain
blocked unless their outputs already exist in the reviewed artifact registry.
Python scientific stages dispatch through registered node runners when their
prerequisites are present.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC
from pathlib import Path
from typing import Any

from src.backend.app.planner.approval_gate import check_approval_gate
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.runtime.node_registry import NodeExecutionContext, get_node_runner
from src.backend.app.schemas.pipeline_schema import PipelineNode
from src.backend.app.schemas.preprocessing_pipeline import (
    PreprocessingPipelineExecuteRequest,
    PreprocessingPipelineExecuteResponse,
    PreprocessingPipelineStageResult,
)
from src.backend.app.schemas.preprocessing_run import PreprocessingStageStatus
from src.backend.app.schemas.preprocessing_stage_catalog import (
    SPM_COREG_NORM_ENV_FLAGS,
    SPM_SMOOTHING_ENV_FLAGS,
    SPM_STAGE_ENV_FLAGS,
    PreprocessingStageSpec,
    get_preprocessing_stage_spec,
    iter_preprocessing_stage_specs,
    normalize_stage_execution_status,
)
from src.backend.app.services.preprocessing_artifact_registry import (
    REGISTRY_FILENAME,
    append_stage_output_artifacts,
    load_artifact_registry,
)
from src.backend.app.services.preprocessing_run import _stage_status_from_spec

_FC_MINIMAL_STAGES = {
    "input_validation",
    "dummy_scan_removal",
    "realignment",
    "nuisance_regression",
    "temporal_filtering",
    "functional_connectivity",
    "subject_qc",
    "group_summary",
}

_NATIVE_NODE_BY_STAGE = {
    "slice_timing": "native_preproc_slice_timing",
    "realignment": "native_preproc_realignment",
    "t1_coregistration": "native_preproc_coregistration",
    "segmentation": "native_preproc_segmentation",
    "normalization": "native_preproc_normalization",
    "spatial_smoothing": "native_preproc_smoothing",
}

_EXTERNAL_SPM_NODE_BY_STAGE = {
    "slice_timing": "spm_slice_timing_subject",
    "realignment": "spm_realign_subject",
    "t1_coregistration": "spm_coregister_subject",
    "segmentation": "spm_segment_subject",
    "normalization": "spm_normalize_subject",
    "spatial_smoothing": "spm_smooth_subject",
}
_EXTERNAL_ENV_FLAGS_BY_STAGE = {
    "slice_timing": SPM_STAGE_ENV_FLAGS,
    "realignment": SPM_STAGE_ENV_FLAGS,
    "t1_coregistration": SPM_COREG_NORM_ENV_FLAGS,
    "segmentation": SPM_COREG_NORM_ENV_FLAGS,
    "normalization": SPM_COREG_NORM_ENV_FLAGS,
    "spatial_smoothing": SPM_SMOOTHING_ENV_FLAGS,
}

_NODE_BY_STAGE = {
    **_NATIVE_NODE_BY_STAGE,
    "nuisance_regression": "nuisance_regression_subject",
    "temporal_filtering": "temporal_filtering_subject",
    "alff_falff": "alff_falff_subject",
    "reho": "reho_subject",
    "functional_connectivity": "functional_connectivity_subject",
    "group_summary": "group_dataset_summary",
}

_COMPLETION_ARTIFACTS = {
    "input_validation": {"input_inventory"},
    "realignment": {"realigned_bold", "motion_parameters", "fd_timeseries"},
    "t1_coregistration": {"coregistered_t1w"},
    "segmentation": {"segmentation_maps"},
    "normalization": {"normalized_bold"},
    "spatial_smoothing": {"smoothed_bold"},
    "nuisance_regression": {"denoised_bold", "confounds_tsv"},
    "temporal_filtering": {"filtered_bold"},
    "alff_falff": {"alff_map"},
    "reho": {"reho_map"},
    "functional_connectivity": {"fc_matrix"},
    "subject_qc": {"qc_json"},
    "group_summary": {"pipeline_report"},
}

_METADATA_ARTIFACT_TYPES = {"stage_manifest", "qc_json", "provenance_json"}
_EXTERNAL_BACKENDS = {"spm12", "matlab-spm", "matlab_spm", "dpabi", "matlab-dpabi", "matlab_dpabi", "matlab"}


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _backend_requires_external_tool(backend: str) -> bool:
    return str(backend or "").strip().lower() in _EXTERNAL_BACKENDS


def _node_id_for_stage(stage_id: str, backend: str = "") -> str:
    if _backend_requires_external_tool(backend):
        return _EXTERNAL_SPM_NODE_BY_STAGE.get(stage_id, _NODE_BY_STAGE.get(stage_id, ""))
    return _NODE_BY_STAGE.get(stage_id, "")


def _safety_flags(*, external_executed: bool = False) -> dict[str, bool]:
    return {
        "rawdata_not_modified": True,
        "converted_input_not_modified": True,
        "no_unreviewed_external_execution": True,
        "no_external_tools_executed": not external_executed,
        "approval_gate_enforced": True,
        "research_use_only": True,
        "clinical_use_prohibited": True,
    }


def _build_stage_statuses(manifest: dict[str, Any]) -> list[PreprocessingStageStatus]:
    statuses = [_stage_status_from_spec(spec) for spec in iter_preprocessing_stage_specs()]
    restored = {
        str(item.get("stage_id")): item
        for item in manifest.get("stage_statuses", [])
        if isinstance(item, dict) and item.get("stage_id")
    }
    for stage in statuses:
        old = restored.get(stage.stage_id)
        if not old:
            continue
        for key, value in old.items():
            if hasattr(stage, key):
                setattr(stage, key, value)
        stage.status = normalize_stage_execution_status(str(old.get("status", stage.status)))
    return statuses


def _stage_mode(
    request: PreprocessingPipelineExecuteRequest,
    spec: PreprocessingStageSpec,
) -> str:
    explicit = request.stages.get(spec.stage_id)
    if explicit:
        return explicit
    if request.pipeline_profile == "custom":
        return "disabled"
    if request.pipeline_profile == "fc_minimal":
        if spec.stage_id == "dummy_scan_removal":
            return "auto"
        return "enabled" if spec.stage_id in _FC_MINIMAL_STAGES else "disabled"
    return "enabled"


def _enabled_stage_ids(request: PreprocessingPipelineExecuteRequest) -> set[str]:
    enabled: set[str] = set()
    for spec in iter_preprocessing_stage_specs():
        mode = _stage_mode(request, spec)
        if mode in {"enabled", "auto"}:
            enabled.add(spec.stage_id)
    return enabled


def _project_root(project_dir: str) -> Path | None:
    return Path(project_dir).expanduser().resolve() if project_dir else None


def _resolve_artifact_path(path_value: str, project_root: Path | None) -> Path:
    path = Path(path_value)
    if not path.is_absolute() and project_root:
        return project_root / path_value
    return path


def _registry_artifacts_by_type(registry: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for artifact in registry.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        by_type.setdefault(str(artifact.get("artifact_type") or ""), []).append(artifact)
    return by_type


def _artifact_paths(
    registry: dict[str, Any],
    project_root: Path | None,
    artifact_type: str,
) -> list[Path]:
    return [
        _resolve_artifact_path(str(item.get("path") or ""), project_root)
        for item in _registry_artifacts_by_type(registry).get(artifact_type, [])
        if item.get("path")
    ]


def _stage_has_completion_artifacts(stage_id: str, registry: dict[str, Any]) -> bool:
    needed = _COMPLETION_ARTIFACTS.get(stage_id, set())
    if not needed:
        return False
    by_type = _registry_artifacts_by_type(registry)
    return all(by_type.get(artifact_type) for artifact_type in needed)


def _subjects_from_registry(registry: dict[str, Any], max_subjects: int | None) -> list[str]:
    inventory = registry.get("input_inventory", {}) if isinstance(registry, dict) else {}
    subjects = [str(s) for s in inventory.get("subjects", []) if str(s)]
    if not subjects:
        seen: set[str] = set()
        for artifact in registry.get("artifacts", []):
            if isinstance(artifact, dict) and artifact.get("subject_id"):
                seen.add(str(artifact.get("subject_id")))
        subjects = sorted(seen)
    if max_subjects is not None and max_subjects >= 0:
        subjects = subjects[:max_subjects]
    return subjects


def _subject_execution_scope(
    registry: dict[str, Any],
    request: PreprocessingPipelineExecuteRequest,
) -> dict[str, Any]:
    all_subjects = _subjects_from_registry(registry, None)
    limit_kind = ""
    limit_value: int | None = None
    if request.execution_limits.preview_limit is not None:
        limit_kind = "preview_limit"
        limit_value = max(int(request.execution_limits.preview_limit), 0)
    elif request.execution_limits.max_subjects is not None:
        limit_kind = "max_subjects"
        limit_value = max(int(request.execution_limits.max_subjects), 0)
    subjects = all_subjects[:limit_value] if limit_value is not None else all_subjects
    return {
        "subjects_total": len(all_subjects),
        "subjects_selected": len(subjects),
        "subjects": subjects,
        "limit_kind": limit_kind,
        "limit_value": limit_value,
        "preview_only": limit_kind == "preview_limit",
        "partial": limit_kind == "max_subjects" and len(subjects) < len(all_subjects),
    }


def _missing_confirmations(request: PreprocessingPipelineExecuteRequest) -> list[str]:
    confirmations = request.confirmations
    missing: list[str] = []
    if not confirmations.confirm_rawdata_readonly:
        missing.append("confirm_rawdata_readonly")
    if not confirmations.confirm_reviewed_execution:
        missing.append("confirm_reviewed_execution")
    if not confirmations.confirm_research_use_only:
        missing.append("confirm_research_use_only")
    if not confirmations.confirm_no_clinical_use:
        missing.append("confirm_no_clinical_use")
    return missing


def _external_approval_gate(
    enabled_specs: list[PreprocessingStageSpec],
    request: PreprocessingPipelineExecuteRequest,
) -> dict[str, Any]:
    external_nodes = [
        _node_id_for_stage(spec.stage_id, _stage_backend(spec.stage_id, request, spec))
        for spec in enabled_specs
        if _backend_requires_external_tool(_stage_backend(spec.stage_id, request, spec))
    ]
    if not external_nodes:
        return {
            "ok": True,
            "execution_allowed": True,
            "approval_required": False,
            "approved": False,
            "errors": [],
            "warnings": [],
        }
    plan = {
        "nodes": [
            {
                "id": node_id,
                "backend": "matlab-spm",
            }
            for node_id in external_nodes
        ]
    }
    validation = {
        "ok": True,
        "approval_required_nodes": external_nodes,
        "high_risk_nodes": external_nodes,
        "risk_summary": {"requires_approval": True},
    }
    return check_approval_gate(plan, validation, request.approval).to_dict()


def _missing_stage_inputs(
    stage_id: str,
    registry: dict[str, Any],
    request: PreprocessingPipelineExecuteRequest,
) -> list[str]:
    by_type = _registry_artifacts_by_type(registry)

    def missing_all(*artifact_types: str) -> list[str]:
        return [artifact_type for artifact_type in artifact_types if not by_type.get(artifact_type)]

    if stage_id == "input_validation":
        return [] if by_type.get("converted_bold") or by_type.get("converted_t1w") else ["converted_bold_or_t1w"]
    if stage_id == "dummy_scan_removal":
        return [] if by_type.get("converted_bold") else ["converted_bold"]
    if stage_id == "realignment":
        return [] if (
            by_type.get("slice_timing_corrected_bold")
            or by_type.get("dummy_removed_bold")
            or by_type.get("converted_bold")
        ) else ["slice_timing_corrected_bold_or_dummy_removed_bold_or_converted_bold"]
    if stage_id == "nuisance_regression":
        missing = missing_all("realigned_bold", "motion_parameters", "fd_timeseries")
        if request.nuisance.include_wm_csf and not by_type.get("segmentation_maps"):
            missing.append("segmentation_maps")
        return missing
    if stage_id == "temporal_filtering":
        missing = missing_all("denoised_bold")
        has_tr = request.filtering.tr is not None or request.filtering.fallback_tr is not None
        if not by_type.get("sidecar_json") and not has_tr:
            missing.append("sidecar_json_or_explicit_tr")
        return missing
    if stage_id == "functional_connectivity":
        missing = missing_all("filtered_bold")
        if not request.atlas.atlas_path and not by_type.get("atlas"):
            missing.append("atlas")
        return missing
    if stage_id == "subject_qc":
        return missing_all("fc_matrix")
    if stage_id == "group_summary":
        return missing_all("fc_matrix")

    spec = get_preprocessing_stage_spec(stage_id)
    required = [
        artifact_type for artifact_type in spec.input_artifact_types
        if artifact_type not in _METADATA_ARTIFACT_TYPES
    ]
    return missing_all(*required)


def _stage_backend(stage_id: str, request: PreprocessingPipelineExecuteRequest, spec: PreprocessingStageSpec) -> str:
    policy = request.backend_policy
    if stage_id == "slice_timing":
        return policy.slice_timing
    if stage_id == "realignment":
        return policy.motion_correction
    if stage_id == "t1_coregistration":
        return policy.t1_coregistration
    if stage_id == "segmentation":
        return policy.segmentation
    if stage_id == "normalization":
        return policy.normalization
    if stage_id == "spatial_smoothing":
        return policy.spatial_smoothing
    if stage_id == "nuisance_regression":
        return policy.nuisance_regression
    if stage_id == "temporal_filtering":
        return policy.temporal_filtering
    if stage_id == "functional_connectivity":
        return policy.functional_connectivity
    if stage_id == "alff_falff":
        return policy.alff_falff
    if stage_id == "reho":
        return policy.reho
    return spec.default_backend


def _node_params(
    stage_id: str,
    request: PreprocessingPipelineExecuteRequest,
    registry: dict[str, Any],
    project_root: Path | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if stage_id == "nuisance_regression":
        params.update(
            {
                "backend": request.backend_policy.nuisance_regression,
                "model": request.nuisance.model,
                "include_intercept": request.nuisance.include_intercept,
                "include_linear_trend": request.nuisance.include_linear_trend,
                "include_global_signal": request.nuisance.include_global_signal,
            }
        )
        realigned = _artifact_paths(registry, project_root, "realigned_bold")
        motion = _artifact_paths(registry, project_root, "motion_parameters")
        if realigned:
            params["input_nii"] = str(realigned[-1])
        if motion:
            params["motion_parameter_file"] = str(motion[-1])
    elif stage_id == "temporal_filtering":
        params.update(
            {
                "backend": request.backend_policy.temporal_filtering,
                "low_hz": request.filtering.low_hz,
                "high_hz": request.filtering.high_hz,
                "tr": request.filtering.tr,
                "fallback_tr": request.filtering.fallback_tr,
            }
        )
    elif stage_id == "functional_connectivity":
        filtered = _artifact_paths(registry, project_root, "filtered_bold")
        atlas = request.atlas.atlas_path or (
            str(_artifact_paths(registry, project_root, "atlas")[-1])
            if _artifact_paths(registry, project_root, "atlas") else ""
        )
        params.update(
            {
                "backend": request.backend_policy.functional_connectivity,
                "atlas_path": atlas or None,
                "labels_path": request.atlas.labels_path or None,
                "input_nii": str(filtered[-1]) if filtered else None,
            }
        )
    elif stage_id == "alff_falff":
        params.update(
            {
                "backend": request.backend_policy.alff_falff,
                "low_hz": request.filtering.low_hz,
                "high_hz": request.filtering.high_hz,
                "tr": request.filtering.tr,
                "fallback_tr": request.filtering.fallback_tr,
            }
        )
    elif stage_id == "reho":
        params.update({"backend": request.backend_policy.reho})
    return params


def _collect_output_paths(result: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for key in ("outputs", "output_paths"):
        value = result.get(key)
        if isinstance(value, list):
            paths.extend(Path(str(item)) for item in value if str(item))
    for key in ("output_nii", "confounds_tsv", "result_json", "qc_json", "provenance_path"):
        value = result.get(key)
        if value:
            paths.append(Path(str(value)))
    for value in result.values():
        if isinstance(value, dict):
            paths.extend(_collect_output_paths(value))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _artifact_type_for_output(stage_id: str, path: Path) -> str:
    name = path.name.lower()
    suffixes = "".join(path.suffixes).lower()
    if "provenance" in name and path.suffix.lower() == ".json":
        return "provenance_json"
    if "qc" in name and path.suffix.lower() == ".json":
        return "qc_json"
    if path.suffix.lower() == ".md" and ("qc" in name or "report" in name):
        return "qc_markdown"
    if "confounds" in name and path.suffix.lower() in {".tsv", ".csv"}:
        return "confounds_tsv"
    if stage_id == "nuisance_regression" and suffixes.endswith((".nii", ".nii.gz")):
        return "denoised_bold"
    if stage_id == "temporal_filtering" and suffixes.endswith((".nii", ".nii.gz")):
        return "filtered_bold"
    if "denoised" in name and suffixes.endswith((".nii", ".nii.gz")):
        return "denoised_bold"
    if ("filtered" in name or name.startswith("filt_")) and suffixes.endswith((".nii", ".nii.gz")):
        return "filtered_bold"
    if "roi_timeseries" in name:
        return "roi_timeseries"
    if name in {"labels.json", "labels.tsv", "roi_definitions.json"}:
        return "roi_labels"
    if "motion_qc_summary" in name:
        return "motion_qc_summary"
    if "fd_timeseries" in name:
        return "fd_timeseries"
    if stage_id == "functional_connectivity" and suffixes.endswith((".nii", ".nii.gz")) and "atlas" in name:
        return "atlas"
    if "fisher" in name:
        return "fisher_z_matrix"
    if "correlation" in name or "fc_matrix" in name:
        return "fc_matrix"
    if "alff" in name and "falff" not in name:
        return "alff_map"
    if "falff" in name:
        return "falff_map"
    if "reho" in name:
        return "reho_map"
    if path.suffix.lower() == ".json":
        return "stage_manifest"
    outputs = get_preprocessing_stage_spec(stage_id).output_artifact_types
    for artifact_type in outputs:
        if artifact_type not in _METADATA_ARTIFACT_TYPES:
            return artifact_type
    return "stage_manifest"


def _append_runner_outputs(
    *,
    registry_path: Path,
    project_id: str,
    preprocessing_run_id: str,
    stage_id: str,
    result: dict[str, Any],
    project_dir: str,
    execution_id: str,
    backend: str,
) -> list[str]:
    existing_paths = [path for path in _collect_output_paths(result) if path.exists()]
    if not existing_paths:
        return []
    by_type: dict[str, list[Path]] = {}
    for path in existing_paths:
        by_type.setdefault(_artifact_type_for_output(stage_id, path), []).append(path)
    appended = append_stage_output_artifacts(
        registry_path=registry_path,
        project_id=project_id,
        preprocessing_run_id=preprocessing_run_id,
        stage_id=stage_id,
        output_paths_by_type=by_type,
        project_dir=project_dir,
        source_execution_id=execution_id,
        backend=backend,
        metadata={"reviewed_orchestrator": True},
    )
    return list(appended.get("appended_artifact_ids", []))


def _execute_python_node(
    *,
    stage_id: str,
    context: NodeExecutionContext,
    request: PreprocessingPipelineExecuteRequest,
    registry: dict[str, Any],
    project_root: Path | None,
    subjects: list[str],
) -> dict[str, Any]:
    node_id = _NODE_BY_STAGE.get(stage_id)
    if not node_id:
        return {"ok": False, "errors": [f"No registered node mapping for stage: {stage_id}"]}
    runner = get_node_runner(node_id)
    node = PipelineNode(
        id=node_id,
        name=node_id,
        agent="system",
        backend="python",
        params=_node_params(stage_id, request, registry, project_root),
        parallel_level="subject" if stage_id != "group_summary" else "project",
    )
    if stage_id == "group_summary":
        return runner(context, node)
    if not subjects:
        return {"ok": False, "errors": [f"No subjects available for stage: {stage_id}"]}
    results: list[dict[str, Any]] = []
    for subject_id in subjects:
        results.append(runner(context, node, {"subject_id": subject_id}, subject_id))
    failed = [item for item in results if not item.get("ok")]
    preview_subjects = [
        item for item in results
        if item.get("preview_only") or item.get("stage_status") == "preview_only"
    ]
    metadata_only_subjects = [
        item for item in results
        if item.get("metadata_only") or item.get("stage_status") == "metadata_only"
    ]
    if failed:
        status = "partial" if len(failed) < len(results) else "failed"
    elif request.execution_limits.preview_limit is not None or preview_subjects:
        status = "preview_only"
    elif request.execution_limits.max_subjects is not None or metadata_only_subjects:
        status = "partial" if request.execution_limits.max_subjects is not None else "metadata_only"
    else:
        status = "succeeded"
    return {
        "ok": not failed,
        "status": status,
        "node_id": node_id,
        "subject_results": results,
        "outputs": [
            output
            for item in results
            for output in item.get("outputs", [])
            if output
        ],
        "warnings": [
            warning
            for item in results
            for warning in item.get("warnings", [])
            if warning
        ] + (
            [f"preview_limit={request.execution_limits.preview_limit} restricted subject execution."]
            if request.execution_limits.preview_limit is not None else []
        ) + (
            [f"max_subjects={request.execution_limits.max_subjects} restricted subject execution."]
            if request.execution_limits.max_subjects is not None else []
        ),
        "errors": [
            error
            for item in failed
            for error in item.get("errors", [])
            if error
        ],
    }


def _write_stage_manifest(
    stage_dir: Path,
    payload: dict[str, Any],
) -> str:
    stage_dir.mkdir(parents=True, exist_ok=True)
    path = stage_dir / "stage_result.json"
    atomic_write_json(path, payload, schema_version=1)
    return str(path)


def _latest_artifact_path(
    registry: dict[str, Any],
    project_root: Path | None,
    *artifact_types: str,
) -> str:
    for artifact_type in artifact_types:
        paths = _artifact_paths(registry, project_root, artifact_type)
        if paths:
            return str(paths[-1])
    return ""


def _reviewed_stage_overrides(enabled_ids: set[str]) -> dict[str, bool]:
    fc_enabled = "functional_connectivity" in enabled_ids
    return {
        "dummy_scan_removal": "dummy_scan_removal" in enabled_ids,
        "slice_timing": "slice_timing" in enabled_ids,
        "realignment": "realignment" in enabled_ids,
        "motion_qc": "realignment" in enabled_ids,
        "coregistration": "t1_coregistration" in enabled_ids,
        "segmentation": "segmentation" in enabled_ids,
        "normalization": "normalization" in enabled_ids,
        "smoothing": "spatial_smoothing" in enabled_ids,
        "nuisance_regression": "nuisance_regression" in enabled_ids,
        "detrending": "temporal_filtering" in enabled_ids,
        "temporal_filtering": "temporal_filtering" in enabled_ids,
        "alff": "alff_falff" in enabled_ids,
        "falff": "alff_falff" in enabled_ids,
        "reho": "reho" in enabled_ids,
        "atlas_resampling": fc_enabled,
        "roi_timeseries": fc_enabled,
        "functional_connectivity": fc_enabled,
        "subject_qc": "subject_qc" in enabled_ids,
        "group_summary": "group_summary" in enabled_ids,
    }


def _native_full_request_from_reviewed(
    *,
    request: PreprocessingPipelineExecuteRequest,
    preprocessing_run_id: str,
    execution_dir: Path,
    registry: dict[str, Any],
    project_root: Path | None,
    enabled_ids: set[str],
    subjects: list[str],
) -> Any:
    from src.backend.app.schemas.native_preproc_api import (  # noqa: E402
        NativeFullPreprocConfirmations,
        NativeFullPreprocRequest,
    )

    input_bold = _latest_artifact_path(
        registry,
        project_root,
        "converted_bold",
        "dummy_removed_bold",
        "slice_timing_corrected_bold",
        "realigned_bold",
    )
    return NativeFullPreprocRequest(
        run_id=f"{preprocessing_run_id}-native-full",
        subject_id=subjects[0] if subjects else "",
        output_dir=str(execution_dir / "native_full"),
        input_bold=input_bold,
        sidecar_json=_latest_artifact_path(registry, project_root, "sidecar_json"),
        t1w=_latest_artifact_path(registry, project_root, "converted_t1w", "t1w"),
        template=_latest_artifact_path(registry, project_root, "template"),
        atlas=request.atlas.atlas_path or _latest_artifact_path(registry, project_root, "atlas"),
        atlas_labels=request.atlas.labels_path,
        enable_slice_timing="slice_timing" in enabled_ids,
        tr=request.filtering.tr or request.filtering.fallback_tr,
        low_hz=request.filtering.low_hz,
        high_hz=request.filtering.high_hz,
        include_wm=request.nuisance.include_wm_csf,
        include_csf=request.nuisance.include_wm_csf,
        include_global_signal=request.nuisance.include_global_signal,
        stage_overrides=_reviewed_stage_overrides(enabled_ids),
        confirmations=NativeFullPreprocConfirmations(
            confirm_reviewed_native_execution=request.confirmations.confirm_reviewed_execution,
            confirm_rawdata_readonly=request.confirmations.confirm_rawdata_readonly,
            confirm_no_external_tools=True,
            confirm_research_use_only=request.confirmations.confirm_research_use_only,
            confirm_no_clinical_use=request.confirmations.confirm_no_clinical_use,
        ),
    )


_NATIVE_TO_REVIEWED_STAGE = {
    "coregistration": "t1_coregistration",
    "smoothing": "spatial_smoothing",
    "alff": "alff_falff",
    "falff": "alff_falff",
}


def _reviewed_result_from_native_stage(native_stage: Any) -> PreprocessingPipelineStageResult:
    stage_id = _NATIVE_TO_REVIEWED_STAGE.get(native_stage.stage_id, native_stage.stage_id)
    artifacts = list(native_stage.output_artifacts or [])
    return PreprocessingPipelineStageResult(
        stage_id=stage_id,
        name=native_stage.display_name,
        status=native_stage.status,
        enabled=native_stage.status != "skipped",
        optional=False,
        backend=native_stage.backend,
        node_id=native_stage.node_id,
        blocking_issues=list(native_stage.blocking_issues),
        warnings=list(native_stage.warnings),
        errors=list(native_stage.errors),
        output_artifact_ids=[
            str(item.get("artifact_id"))
            for item in artifacts
            if isinstance(item, dict) and item.get("artifact_id")
        ],
        result={
            "native_stage_id": native_stage.stage_id,
            "capability_level": native_stage.capability_level,
            "validation_status": native_stage.validation_status,
            "input_artifacts": list(native_stage.input_artifacts or []),
            "output_artifacts": artifacts,
            "native_result": dict(native_stage.result or {}),
        },
    )


def _map_native_stage_ids(stage_ids: list[str]) -> list[str]:
    return sorted({_NATIVE_TO_REVIEWED_STAGE.get(stage_id, stage_id) for stage_id in stage_ids})


def _execute_reviewed_native_full(
    *,
    project_id: str,
    preprocessing_run_id: str,
    request: PreprocessingPipelineExecuteRequest,
    run_manifest: dict[str, Any],
    manifest_path: Path,
    registry: dict[str, Any],
    registry_path: Path,
    project_root: Path | None,
    enabled_ids: set[str],
    stage_statuses: list[PreprocessingStageStatus],
    execution_id: str,
    execution_dir: Path,
    subjects: list[str],
    effective_project_dir: str,
    execution_scope: dict[str, Any],
) -> PreprocessingPipelineExecuteResponse:
    from src.backend.app.native_preproc.orchestrator.runner import (
        execute_native_full_preproc,  # noqa: E402
    )

    native_request = _native_full_request_from_reviewed(
        request=request,
        preprocessing_run_id=preprocessing_run_id,
        execution_dir=execution_dir,
        registry=registry,
        project_root=project_root,
        enabled_ids=enabled_ids,
        subjects=subjects,
    )
    native_response = execute_native_full_preproc(
        project_id,
        native_request,
        project_dir=effective_project_dir,
    )
    stage_results = [_reviewed_result_from_native_stage(item) for item in native_response.stage_results]
    status_by_id = {item.stage_id: item.status for item in stage_results}
    for stage in stage_statuses:
        if stage.stage_id in status_by_id:
            stage.status = normalize_stage_execution_status(status_by_id[stage.stage_id])
            stage.backend = "native_python"
            stage.output_manifest = next(
                (
                    item.result
                    for item in stage_results
                    if item.stage_id == stage.stage_id
                ),
                {},
            )
            stage.registered_at = _now_iso()
    safety_flags = {
        **_safety_flags(),
        **native_response.safety_flags,
        "reviewed_native_full_delegated": True,
    }
    approval_gate = {
        "ok": True,
        "execution_allowed": True,
        "approval_required": False,
        "approved": False,
        "native_full_delegated": True,
    }
    execution_manifest = {
        "project_id": project_id,
        "preprocessing_run_id": preprocessing_run_id,
        "execution_id": execution_id,
        "pipeline_profile": request.pipeline_profile,
        "status": native_response.status,
        "created_at": _now_iso(),
        "request": request.model_dump(mode="json"),
        "native_full_request": native_request.model_dump(mode="json"),
        "native_full_manifest_path": native_response.manifest_path,
        "approval_gate": approval_gate,
        "stage_results": [item.model_dump(mode="json") for item in stage_results],
        "artifact_registry_path": str(registry_path),
        "native_full_run_dir": native_response.run_dir,
        "report_path": native_response.final_report_path,
        "validation_status": native_response.status,
        "execution_scope": {key: value for key, value in execution_scope.items() if key != "subjects"},
        "safety_flags": safety_flags,
    }
    reviewed_manifest_path = execution_dir / "preprocessing_reviewed_execution_manifest.json"
    atomic_write_json(reviewed_manifest_path, execution_manifest, schema_version=1)

    run_manifest["status"] = f"reviewed_execution_{native_response.status}"
    run_manifest["updated_at"] = _now_iso()
    run_manifest["reviewed_execution_manifest_path"] = str(reviewed_manifest_path)
    run_manifest["native_full_manifest_path"] = native_response.manifest_path
    run_manifest["stage_statuses"] = [stage.model_dump() for stage in stage_statuses]
    artifacts = run_manifest.setdefault("artifacts", {})
    artifacts["reviewed_execution_manifest"] = str(reviewed_manifest_path)
    artifacts["native_full_manifest"] = native_response.manifest_path
    if native_response.final_report_path:
        artifacts["latest_pipeline_report"] = native_response.final_report_path
    atomic_write_json(manifest_path, run_manifest, schema_version=1)

    return PreprocessingPipelineExecuteResponse(
        ok=native_response.ok,
        status=native_response.status,
        project_id=project_id,
        preprocessing_run_id=preprocessing_run_id,
        execution_id=execution_id,
        pipeline_profile=request.pipeline_profile,
        manifest_path=str(reviewed_manifest_path),
        artifact_registry_path=str(registry_path),
        report_path=native_response.final_report_path,
        validation_status=native_response.status,
        completed_stages=_map_native_stage_ids(native_response.completed_stages),
        skipped_stages=_map_native_stage_ids(native_response.skipped_stages),
        blocked_stages=_map_native_stage_ids(native_response.blocked_stages),
        failed_stages=_map_native_stage_ids(native_response.failed_stages),
        metadata_only_stages=_map_native_stage_ids(native_response.metadata_only_stages),
        preview_only_stages=[],
        stage_results=stage_results,
        stage_statuses=stage_statuses,
        approval_gate=approval_gate,
        warnings=list(native_response.warnings),
        errors=list(native_response.errors),
        blocking_issues=list(native_response.blocking_issues),
        next_actions=list(native_response.next_actions),
        safety_flags=safety_flags,
    )


def execute_reviewed_preprocessing_pipeline(
    project_id: str,
    preprocessing_run_id: str,
    request: PreprocessingPipelineExecuteRequest,
    *,
    project_dir: str = "",
    store: Any,
) -> PreprocessingPipelineExecuteResponse:
    project = store.get_project(project_id)
    if not project:
        return PreprocessingPipelineExecuteResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            preprocessing_run_id=preprocessing_run_id,
            blocking_issues=[f"Project not found: {project_id}"],
            safety_flags=_safety_flags(),
        )

    meta = project.metadata if isinstance(project.metadata, dict) else {}
    effective_pd = project_dir or str(meta.get("project_dir") or "")
    run_dir = Path(effective_pd) / "preprocessing_runs" / preprocessing_run_id if effective_pd else Path(f"outputs/preprocessing_runs/{preprocessing_run_id}")
    manifest_path = run_dir / "preprocessing_run_manifest.json"
    if not manifest_path.exists():
        return PreprocessingPipelineExecuteResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            preprocessing_run_id=preprocessing_run_id,
            blocking_issues=["Run manifest not found. Create a preprocessing run first."],
            safety_flags=_safety_flags(),
        )

    execution_id = "pprev-" + hashlib.sha256(
        f"{project_id}:{preprocessing_run_id}:{_now_iso()}".encode()
    ).hexdigest()[:12]
    execution_dir = run_dir / "reviewed_execution" / execution_id
    execution_dir.mkdir(parents=True, exist_ok=True)

    run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry_path = Path(
        str(run_manifest.get("artifact_registry_path") or run_manifest.get("artifacts", {}).get("artifact_registry") or run_dir / REGISTRY_FILENAME)
    )
    if not registry_path.is_absolute():
        registry_path = run_dir / registry_path if registry_path.name != REGISTRY_FILENAME else run_dir / REGISTRY_FILENAME
    registry = load_artifact_registry(registry_path) if registry_path.exists() else {}
    project_root = _project_root(effective_pd)
    execution_scope = _subject_execution_scope(registry, request)
    subjects = [str(item) for item in execution_scope["subjects"]]

    stage_statuses = _build_stage_statuses(run_manifest)
    status_by_id = {stage.stage_id: stage.status for stage in stage_statuses}
    stage_by_id = {stage.stage_id: stage for stage in stage_statuses}
    enabled_ids = _enabled_stage_ids(request)
    enabled_specs = [
        spec for spec in iter_preprocessing_stage_specs()
        if spec.stage_id in enabled_ids
    ]

    approval_gate = _external_approval_gate(enabled_specs, request)
    missing_confirms = _missing_confirmations(request)
    global_blocking = [f"Missing reviewed execution confirmation: {name}" for name in missing_confirms]

    context = NodeExecutionContext(
        run_id=preprocessing_run_id,
        project_config={
            "runtime": {
                "work_dir": str(run_dir / "work"),
                "log_dir": str(run_dir / "logs"),
                "report_dir": str(run_dir / "reports"),
                "matlab_command": "matlab",
                "derivatives_dir": request.derivatives_dir or str(Path(effective_pd) / "derivatives" if effective_pd else run_dir / "derivatives"),
            },
            "third_party": {"spm_dir": "", "dpabi_dir": ""},
        },
        work_dir=str(run_dir / "work"),
        log_dir=str(run_dir / "logs"),
        matlab_command="matlab",
        spm_dir="",
        dpabi_dir="",
        derivatives_dir=request.derivatives_dir or str(Path(effective_pd) / "derivatives" if effective_pd else run_dir / "derivatives"),
    )

    stage_results: list[PreprocessingPipelineStageResult] = []
    completed: list[str] = []
    skipped: list[str] = []
    blocked: list[str] = []
    failed: list[str] = []
    metadata_only: list[str] = []
    preview_only: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    if execution_scope["preview_only"]:
        warnings.append(
            f"preview_limit={execution_scope['limit_value']} restricts this reviewed execution; outputs are preview_only."
        )
    elif execution_scope["limit_kind"] == "max_subjects":
        warnings.append(
            f"max_subjects={execution_scope['limit_value']} restricts this reviewed execution; outputs are partial."
        )

    explicit_external_backend = any(
        _backend_requires_external_tool(_stage_backend(spec.stage_id, request, spec))
        for spec in enabled_specs
    )
    # Custom reviewed pipelines intentionally keep the modular orchestrator.
    # It can resume from already registered stage artifacts (for example a
    # reviewed realignment result and its motion parameters), while the native
    # full runner starts from the registered imaging input and owns the complete
    # fc_minimal/dparsfa_like chain.
    if (
        not global_blocking
        and not explicit_external_backend
        and request.pipeline_profile != "custom"
    ):
        return _execute_reviewed_native_full(
            project_id=project_id,
            preprocessing_run_id=preprocessing_run_id,
            request=request,
            run_manifest=run_manifest,
            manifest_path=manifest_path,
            registry=registry,
            registry_path=registry_path,
            project_root=project_root,
            enabled_ids=enabled_ids,
            stage_statuses=stage_statuses,
            execution_id=execution_id,
            execution_dir=execution_dir,
            subjects=subjects,
            effective_project_dir=effective_pd,
            execution_scope=execution_scope,
        )

    for spec in iter_preprocessing_stage_specs():
        mode = _stage_mode(request, spec)
        backend = _stage_backend(spec.stage_id, request, spec)
        stage = stage_by_id[spec.stage_id]
        stage.backend = backend
        stage.enabled = mode in {"enabled", "auto"}
        started_at = _now_iso()
        execution_scope_payload = {
            key: value for key, value in execution_scope.items() if key != "subjects"
        }

        result = PreprocessingPipelineStageResult(
            stage_id=spec.stage_id,
            name=spec.display_name,
            enabled=stage.enabled,
            optional=spec.optional,
            backend=backend,
            node_id=_node_id_for_stage(spec.stage_id, backend),
            started_at=started_at,
        )
        result.result["execution_scope"] = execution_scope_payload

        if mode == "disabled":
            result.status = "skipped"
            result.skipped_reason = "Stage disabled by reviewed pipeline request."
            skipped.append(spec.stage_id)
        elif global_blocking:
            result.status = "blocked"
            result.blocking_issues.extend(global_blocking)
            blocked.append(spec.stage_id)
        elif request.resume and request.rerun_policy == "skip_succeeded" and status_by_id.get(spec.stage_id) in {"succeeded", "partial"}:
            result.status = status_by_id[spec.stage_id]
            result.skipped_reason = "Existing successful stage reused by resume policy."
            completed.append(spec.stage_id)
        elif request.resume and _stage_has_completion_artifacts(spec.stage_id, registry):
            result.status = "succeeded"
            result.skipped_reason = "Completion artifacts already registered; stage reused by resume policy."
            completed.append(spec.stage_id)
        elif mode == "auto" and spec.stage_id == "dummy_scan_removal":
            result.status = "skipped"
            result.skipped_reason = "Dummy scan removal is auto and no dummy-scan count was requested; converted BOLD remains the downstream candidate input."
            skipped.append(spec.stage_id)
        else:
            dependency_issues = [
                f"Dependency {dep} status is {status_by_id.get(dep, 'not_started')}."
                for dep in spec.depends_on
                if dep in enabled_ids and status_by_id.get(dep) not in {"succeeded", "partial", "skipped"}
            ]
            missing_inputs = _missing_stage_inputs(spec.stage_id, registry, request)
            if dependency_issues:
                result.status = "blocked"
                result.blocking_issues.extend(dependency_issues)
                blocked.append(spec.stage_id)
            elif missing_inputs:
                result.status = "blocked"
                result.blocking_issues.extend(f"Missing required input artifact: {item}" for item in missing_inputs)
                blocked.append(spec.stage_id)
            elif _backend_requires_external_tool(backend):
                required_env_flags = _EXTERNAL_ENV_FLAGS_BY_STAGE.get(spec.stage_id, spec.requires_env_flags)
                missing_env = [flag for flag in required_env_flags if not _truthy_env(os.environ.get(flag))]
                if not request.confirmations.confirm_external_tools_if_needed:
                    result.blocking_issues.append("confirm_external_tools_if_needed is required for external-tool stages.")
                if not approval_gate.get("execution_allowed", False):
                    result.blocking_issues.append("Approval Gate did not allow external-tool execution.")
                    for issue in approval_gate.get("errors", []):
                        if isinstance(issue, dict) and issue.get("message"):
                            result.blocking_issues.append(str(issue["message"]))
                if missing_env:
                    result.blocking_issues.append(f"Missing required environment flags: {', '.join(missing_env)}")
                result.blocking_issues.append(
                    "Reviewed orchestrator did not launch MATLAB/SPM; register reviewed SPM sandbox outputs before resuming."
                )
                result.status = "blocked"
                blocked.append(spec.stage_id)
            elif spec.stage_id == "input_validation":
                result.status = "succeeded"
                result.result = {
                    "subjects": subjects,
                    "artifact_registry_path": str(registry_path),
                    "execution_scope": execution_scope_payload,
                }
                completed.append(spec.stage_id)
            elif spec.stage_id in {"subject_qc"}:
                result.status = "succeeded"
                result.result = {
                    "source": "artifact_registry",
                    "fc_artifacts_present": True,
                    "execution_scope": execution_scope_payload,
                }
                completed.append(spec.stage_id)
            elif spec.stage_id in _NODE_BY_STAGE:
                start = time.monotonic()
                node_result = _execute_python_node(
                    stage_id=spec.stage_id,
                    context=context,
                    request=request,
                    registry=registry,
                    project_root=project_root,
                    subjects=subjects,
                )
                node_result.setdefault("execution_scope", execution_scope_payload)
                result.result = node_result
                result.warnings.extend(str(item) for item in node_result.get("warnings", []))
                result.output_artifact_ids = _append_runner_outputs(
                    registry_path=registry_path,
                    project_id=project_id,
                    preprocessing_run_id=preprocessing_run_id,
                    stage_id=spec.stage_id,
                    result=node_result,
                    project_dir=effective_pd,
                    execution_id=execution_id,
                    backend=backend,
                )
                if result.output_artifact_ids and registry_path.exists():
                    registry = load_artifact_registry(registry_path)
                result.result["duration_ms"] = (time.monotonic() - start) * 1000
                if node_result.get("ok") and result.output_artifact_ids:
                    result.status = normalize_stage_execution_status(str(node_result.get("status", "succeeded")))
                    if result.status == "succeeded":
                        completed.append(spec.stage_id)
                    elif result.status == "partial":
                        completed.append(spec.stage_id)
                    elif result.status == "preview_only":
                        preview_only.append(spec.stage_id)
                    elif result.status == "metadata_only":
                        metadata_only.append(spec.stage_id)
                elif node_result.get("ok"):
                    result.status = "metadata_only"
                    result.warnings.append("Runner returned ok without registered numerical artifacts.")
                    metadata_only.append(spec.stage_id)
                else:
                    result.status = "failed"
                    result.errors.extend(str(item) for item in node_result.get("errors", []))
                    failed.append(spec.stage_id)
            else:
                result.status = "blocked"
                result.blocking_issues.append(f"No reviewed runner registered for stage: {spec.stage_id}")
                blocked.append(spec.stage_id)

        result.ended_at = _now_iso()
        result.result.setdefault("execution_scope", execution_scope_payload)
        stage.status = result.status
        stage.output_manifest = result.model_dump()
        stage.error_message = "; ".join([*result.blocking_issues, *result.errors]) or None
        stage.registered_at = result.ended_at
        status_by_id[spec.stage_id] = result.status
        stage_manifest_path = _write_stage_manifest(
            execution_dir / "stages" / spec.stage_id,
            result.model_dump(mode="json"),
        )
        result.result.setdefault("stage_manifest_path", stage_manifest_path)
        stage_results.append(result)

    report_path = ""
    validation_status = ""
    if request.generate_report:
        try:
            from src.backend.app.services.preprocessing_pipeline_report import (
                generate_pipeline_report,
            )

            report = generate_pipeline_report(project_id, preprocessing_run_id, project_dir=effective_pd)
            report_path = report.report_path
            if report_path:
                json_report = Path(report_path) / "preprocessing_pipeline_report.json"
                if json_report.exists():
                    append_stage_output_artifacts(
                        registry_path=registry_path,
                        project_id=project_id,
                        preprocessing_run_id=preprocessing_run_id,
                        stage_id="final_report",
                        output_paths_by_type={"pipeline_report": [json_report]},
                        project_dir=effective_pd,
                        source_execution_id=execution_id,
                        backend="python",
                        metadata={"reviewed_orchestrator": True},
                    )
        except Exception as exc:  # pragma: no cover - defensive report isolation
            warnings.append(f"Pipeline report generation failed: {exc}")
    if request.run_validation:
        try:
            from src.backend.app.services.preprocessing_pipeline_validation import (
                validate_preprocessing_pipeline,
            )

            validation = validate_preprocessing_pipeline(project_id, preprocessing_run_id, project_dir=effective_pd)
            validation_status = validation.status
        except Exception as exc:  # pragma: no cover - defensive validation isolation
            warnings.append(f"Pipeline validation failed: {exc}")

    terminal_failed_required = [
        sid for sid in failed
        if not get_preprocessing_stage_spec(sid).optional
    ]
    terminal_blocked_required = [
        sid for sid in blocked
        if not get_preprocessing_stage_spec(sid).optional
    ]
    if terminal_failed_required:
        response_status = "failed"
    elif terminal_blocked_required:
        response_status = "blocked"
    elif failed or blocked or metadata_only or preview_only:
        response_status = "partial"
    else:
        response_status = "succeeded"

    execution_manifest = {
        "project_id": project_id,
        "preprocessing_run_id": preprocessing_run_id,
        "execution_id": execution_id,
        "pipeline_profile": request.pipeline_profile,
        "status": response_status,
        "created_at": _now_iso(),
        "request": request.model_dump(mode="json"),
        "approval_gate": approval_gate,
        "stage_results": [item.model_dump(mode="json") for item in stage_results],
        "artifact_registry_path": str(registry_path),
        "report_path": report_path,
        "validation_status": validation_status,
        "safety_flags": _safety_flags(),
    }
    reviewed_manifest_path = execution_dir / "preprocessing_reviewed_execution_manifest.json"
    atomic_write_json(reviewed_manifest_path, execution_manifest, schema_version=1)

    run_manifest["status"] = f"reviewed_execution_{response_status}"
    run_manifest["updated_at"] = _now_iso()
    run_manifest["reviewed_execution_manifest_path"] = str(reviewed_manifest_path)
    run_manifest["stage_statuses"] = [stage.model_dump() for stage in stage_statuses]
    artifacts = run_manifest.setdefault("artifacts", {})
    artifacts["reviewed_execution_manifest"] = str(reviewed_manifest_path)
    if report_path:
        artifacts["latest_pipeline_report"] = report_path
    atomic_write_json(manifest_path, run_manifest, schema_version=1)

    next_actions = (
        ["Resolve blocked required stages before continuing."] if terminal_blocked_required
        else ["Review and retry failed required stages."] if terminal_failed_required
        else ["Review generated report and validation before moving to UI work."] if response_status == "succeeded"
        else ["Review partial, metadata_only, or preview_only stages before continuing."]
    )
    return PreprocessingPipelineExecuteResponse(
        ok=response_status == "succeeded",
        status=response_status,
        project_id=project_id,
        preprocessing_run_id=preprocessing_run_id,
        execution_id=execution_id,
        pipeline_profile=request.pipeline_profile,
        manifest_path=str(reviewed_manifest_path),
        artifact_registry_path=str(registry_path),
        report_path=report_path,
        validation_status=validation_status,
        completed_stages=sorted(set(completed)),
        skipped_stages=skipped,
        blocked_stages=blocked,
        failed_stages=failed,
        metadata_only_stages=metadata_only,
        preview_only_stages=preview_only,
        stage_results=stage_results,
        stage_statuses=stage_statuses,
        approval_gate=approval_gate,
        warnings=warnings,
        errors=errors,
        blocking_issues=terminal_blocked_required,
        next_actions=next_actions,
        safety_flags=_safety_flags(),
    )


__all__ = ["execute_reviewed_preprocessing_pipeline"]
