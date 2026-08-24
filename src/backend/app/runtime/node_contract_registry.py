"""Stable node-id to versioned execution-contract registry."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_registry import NODE_REGISTRY
from src.backend.app.schemas.node_contract import (
    ArtifactContract,
    ContractRetryPolicy,
    ContractValidationEvidence,
    IdempotencyPolicy,
    NodeContract,
    ParameterContract,
    ResourceRequirements,
    ValidationPolicy,
)


def _parameter(type_: str, **kwargs: Any) -> ParameterContract:
    return ParameterContract(type=type_, **kwargs)


_COMMON_SUBJECT_PARAMETERS = {
    "dataset_index": _parameter("string", nullable=True, path_access="read"),
}


_STRICT_PARAMETERS: dict[str, dict[str, ParameterContract]] = {
    "contract_smoke": {
        "fail": _parameter("boolean", default=False),
        "message": _parameter("string", default="contract smoke passed"),
        "output_dir": _parameter("string", nullable=True, path_access="write"),
    },
    "data_inspection": {
        "rawdata_dir": _parameter("string", nullable=True, path_access="read"),
        "output_dir": _parameter("string", nullable=True, path_access="write"),
        "read_nifti_metadata": _parameter("boolean", default=True),
    },
    "create_synthetic_bids": {
        "output_dir": _parameter("string", default="./examples/synthetic_bids/rawdata", path_access="write"),
        "subjects": _parameter("array", nullable=True, min_items=1),
    },
    "dataset_evaluation": {
        "dataset_index": _parameter("string", nullable=True, path_access="read"),
        "report_dir": _parameter("string", default="./reports", path_access="write"),
    },
    "environment_check": {},
    "nuisance_regression_subject": {
        **_COMMON_SUBJECT_PARAMETERS,
        "backend": _parameter("string", default="python", enum=("python",)),
        "model": _parameter("string", default="friston24"),
        "include_intercept": _parameter("boolean", default=True),
        "include_linear_trend": _parameter("boolean", default=True),
        "include_global_signal": _parameter("boolean", default=False),
        "input_nii": _parameter("string", nullable=True, path_access="read"),
        "motion_parameter_file": _parameter("string", nullable=True, path_access="read"),
    },
    "temporal_filtering_subject": {
        **_COMMON_SUBJECT_PARAMETERS,
        "backend": _parameter("string", default="python", enum=("python",)),
        "low_hz": _parameter("number", default=0.01, minimum=0.0, maximum=0.25),
        "high_hz": _parameter("number", default=0.08, minimum=0.0, maximum=0.5),
        "tr": _parameter("number", nullable=True, minimum=0.000001),
        "fallback_tr": _parameter("number", nullable=True, minimum=0.000001),
    },
    "alff_falff_subject": {
        **_COMMON_SUBJECT_PARAMETERS,
        "backend": _parameter("string", default="python", enum=("python",)),
        "low_hz": _parameter("number", default=0.01, minimum=0.0, maximum=0.25),
        "high_hz": _parameter("number", default=0.08, minimum=0.0, maximum=0.5),
        "tr": _parameter("number", nullable=True, minimum=0.000001),
        "fallback_tr": _parameter("number", nullable=True, minimum=0.000001),
    },
    "reho_subject": {
        **_COMMON_SUBJECT_PARAMETERS,
        "neighborhood": _parameter("integer", default=27, enum=(7, 19, 27)),
        "use_gm_mask": _parameter("boolean", default=False),
    },
    "functional_connectivity_subject": {
        **_COMMON_SUBJECT_PARAMETERS,
        "backend": _parameter("string", default="python", enum=("python",)),
        "roi_count": _parameter("integer", default=4, minimum=2, maximum=1000),
        "atlas_path": _parameter("string", nullable=True, path_access="read"),
        "labels_path": _parameter("string", nullable=True, path_access="read"),
        "generate_seed_map": _parameter("boolean", default=False),
        "input_nii": _parameter("string", nullable=True, path_access="read"),
    },
    "native_dicom_conversion_execute": {
        "project_id": _parameter("string", required=True, path_access="non_path"),
        "project_dir": _parameter("string", required=True, path_access="write"),
        "rawdata_dir": _parameter("string", required=True, path_access="read"),
        "conversion_run_id": _parameter("string", required=True, path_access="non_path"),
        "output_dir": _parameter("string", nullable=True, path_access="write"),
    },
    "native_auto_acpc_align": {
        "project_id": _parameter("string", nullable=True, path_access="non_path"),
        "project_dir": _parameter("string", required=True, path_access="non_path"),
        "source_t1_artifact_id": _parameter("string", required=True, path_access="non_path"),
        "output_root": _parameter("string", required=True, path_access="write"),
        "template_id": _parameter("string", default="spm12_avg152_t1_ras", enum=("spm12_avg152_t1_ras",)),
        "interpolation": _parameter("string", default="linear", enum=("linear", "cubic")),
    },
    "native_preproc_full_execute": {
        "project_id": _parameter("string", nullable=True, path_access="non_path"),
        "project_dir": _parameter("string", nullable=True, path_access="write"),
        "subject_id": _parameter("string", nullable=True, path_access="non_path"),
        "conversion_run_id": _parameter("string", nullable=True, path_access="non_path"),
        "input_bold": _parameter("string", nullable=True, path_access="read"),
        "input_bids_dir": _parameter("string", nullable=True, path_access="read"),
        "sidecar_json": _parameter("string", nullable=True, path_access="read"),
        "t1w": _parameter("string", nullable=True, path_access="read"),
        "template": _parameter("string", nullable=True, path_access="read"),
        "atlas": _parameter("string", nullable=True, path_access="read"),
        "atlas_labels": _parameter("string", nullable=True, path_access="read"),
        "output_dir": _parameter("string", nullable=True, path_access="write"),
        "confirmations": _parameter("object", required=True),
        "stage_overrides": _parameter("object", default={}),
        "cpu_policy": _parameter("object", default={}),
        "compute_policy": _parameter("object", default={}),
        "overwrite_policy": _parameter(
            "string",
            default="fail_if_exists",
            enum=("fail_if_exists", "write_new_run_directory"),
        ),
        "include_global_signal": _parameter("boolean", default=False),
        "tr": _parameter("number", nullable=True, minimum=0.000001),
    },
    "native_preproc_full_dry_run": {
        "input_bold": _parameter("string", nullable=True, path_access="read"),
        "input_bids_dir": _parameter("string", nullable=True, path_access="read"),
        "conversion_run_id": _parameter("string", nullable=True, path_access="non_path"),
        "sidecar_json": _parameter("string", nullable=True, path_access="read"),
        "output_dir": _parameter("string", nullable=True, path_access="write"),
    },
    "motion_qc_subject": {
        **_COMMON_SUBJECT_PARAMETERS,
        "fd_threshold": _parameter("number", default=0.5, minimum=0.0),
        "head_radius_mm": _parameter("number", default=50.0, minimum=0.000001),
    },
    "subject_qc": {
        **_COMMON_SUBJECT_PARAMETERS,
        "qc_output_dir": _parameter("string", nullable=True, path_access="write"),
    },
    "rsfmri_report_exporter": {
        "exports_dir": _parameter("string", default="./exports", path_access="write"),
        "export_id": _parameter("string", nullable=True, path_access="non_path"),
        "include_subject_qc": _parameter("boolean", default=True),
        "include_metrics": _parameter("boolean", default=True),
        "include_fc": _parameter("boolean", default=True),
        "include_contracts": _parameter("boolean", default=True),
        "include_pipeline_runs": _parameter("boolean", default=True),
    },
    "rsfmri_report_package_validator": {
        "exports_dir": _parameter("string", default="./exports", path_access="read"),
        "export_id": _parameter("string", nullable=True, path_access="non_path"),
        "package_dir": _parameter("string", nullable=True, path_access="read"),
        "zip_path": _parameter("string", nullable=True, path_access="read"),
        "strict": _parameter("boolean", default=False),
    },
    "alff_falff_qc_dataset_report": {},
    "docs_inventory": {},
    "functional_connectivity_qc_dataset_report": {},
    "group_dataset_summary": {},
    "motion_qc_dataset_report": {},
    "normalization_qc_dataset_report": {},
    "nuisance_regression_qc_dataset_report": {},
    "project_release_readiness": {},
    "registration_qc_dataset_report": {},
    "reho_qc_dataset_report": {},
    "rsfmri_preprocessing_plan": {},
    "slice_timing_qc_dataset_report": {},
    "smoothing_qc_dataset_report": {},
    "st_realign_motion_chain_report": {},
    "temporal_filtering_qc_dataset_report": {},
    "tissue_qc_dataset_report": {},
    "data_readiness_check": {
        "executable": _parameter("boolean", default=False, enum=(False,)),
        "dry_run_only": _parameter("boolean", default=True, enum=(True,)),
    },
    "bids_validation_check": {
        "executable": _parameter("boolean", default=False, enum=(False,)),
        "dry_run_only": _parameter("boolean", default=True, enum=(True,)),
    },
    "rsfmri_bold_reference_check": {
        "executable": _parameter("boolean", default=False, enum=(False,)),
        "dry_run_only": _parameter("boolean", default=True, enum=(True,)),
        "inspectable": _parameter("boolean", default=True, enum=(True,)),
    },
    "rsfmri_motion_qc_plan": {
        "executable": _parameter("boolean", default=False, enum=(False,)),
        "dry_run_only": _parameter("boolean", default=True, enum=(True,)),
        "inspectable": _parameter("boolean", default=True, enum=(True,)),
    },
    "rsfmri_preprocessing_plan_stub": {
        "executable": _parameter("boolean", default=False, enum=(False,)),
        "dry_run_only": _parameter("boolean", default=True, enum=(True,)),
    },
    "rsfmri_report_plan_stub": {
        "executable": _parameter("boolean", default=False, enum=(False,)),
        "dry_run_only": _parameter("boolean", default=True, enum=(True,)),
    },
}


# Tool Catalog labels are user-facing descriptions and are not always stable
# artifact identifiers (for example, ``ALFF/fALFF maps``).  Goal Contract
# reachability and Observation matching must use the same canonical artifact
# types that the runners persist.
_STRICT_OUTPUT_ARTIFACT_TYPES: dict[str, tuple[str, ...]] = {
    "native_auto_acpc_align": ("acpc_t1w", "transform_matrix", "acpc_landmarks", "qc_json"),
    "native_dicom_conversion_execute": (
        "converted_nifti",
        "bids_sidecar",
        "conversion_manifest",
        "conversion_provenance",
    ),
    "nuisance_regression_subject": ("residual_bold",),
    "temporal_filtering_subject": ("filtered_bold",),
    "alff_falff_subject": ("alff_map", "falff_map"),
    "reho_subject": ("reho_map",),
    "functional_connectivity_subject": ("fc_matrix",),
    "native_preproc_full_execute": (
        "native_full_run_manifest",
        "validation_report",
        "final_report",
        "residual_bold",
        "filtered_bold",
        "alff_map",
        "falff_map",
        "reho_map",
        "fc_matrix",
    ),
    "native_preproc_full_dry_run": ("native_dry_run_plan",),
    "contract_smoke": ("contract_smoke_report", "contract_smoke_log"),
    "create_synthetic_bids": ("synthetic_bids_dataset",),
    "data_inspection": ("dataset_index",),
    "dataset_evaluation": ("dataset_evaluation_report",),
    "environment_check": ("environment_check",),
    "motion_qc_subject": ("motion_qc",),
    "subject_qc": ("qc_metrics",),
    "rsfmri_report_exporter": ("report_package", "checksum_manifest"),
    "rsfmri_report_package_validator": ("report_package_validation",),
    "alff_falff_qc_dataset_report": ("alff_falff_qc_report",),
    "docs_inventory": ("docs_inventory",),
    "functional_connectivity_qc_dataset_report": ("functional_connectivity_qc_report",),
    "group_dataset_summary": ("group_summary", "dashboard_data"),
    "motion_qc_dataset_report": ("motion_qc_dataset_report",),
    "normalization_qc_dataset_report": ("normalization_qc_report",),
    "nuisance_regression_qc_dataset_report": ("nuisance_regression_qc_report",),
    "project_release_readiness": ("release_readiness_report",),
    "registration_qc_dataset_report": ("registration_qc_report",),
    "reho_qc_dataset_report": ("reho_qc_report",),
    "rsfmri_preprocessing_plan": ("rsfmri_preprocessing_plan",),
    "slice_timing_qc_dataset_report": ("slice_timing_qc_report",),
    "smoothing_qc_dataset_report": ("smoothing_qc_report",),
    "st_realign_motion_chain_report": ("st_realign_motion_chain_report",),
    "temporal_filtering_qc_dataset_report": ("temporal_filtering_qc_report",),
    "tissue_qc_dataset_report": ("tissue_qc_report",),
    "data_readiness_check": ("readiness_summary",),
    "bids_validation_check": ("bids_validation_summary",),
    "rsfmri_bold_reference_check": ("bold_reference_readiness_report",),
    "rsfmri_motion_qc_plan": ("motion_qc_readiness_report",),
    "rsfmri_preprocessing_plan_stub": ("preprocessing_plan_stub",),
    "rsfmri_report_plan_stub": ("report_plan_stub",),
    "spm_realign_subject": (
        "realigned BOLD",
        "mean BOLD",
        "motion parameters",
        "execution logs",
        "provenance",
        "node state",
    ),
}


_STRICT_INPUT_ARTIFACT_TYPES: dict[str, tuple[str, ...]] = {
    "alff_falff_subject": ("filtered_bold",),
    "data_inspection": ("rawdata",),
    "dataset_evaluation": ("dataset_index",),
    "functional_connectivity_subject": ("filtered_bold", "roi_atlas"),
    "motion_qc_subject": ("motion_parameters",),
    "native_auto_acpc_align": ("registered_t1w", "acpc_reference_template"),
    "native_dicom_conversion_execute": ("approved_dicom_mapping", "rawdata_checksum_snapshot"),
    "native_preproc_full_dry_run": ("registered_bold", "bids_sidecar"),
    "native_preproc_full_execute": ("registered_bold", "bids_sidecar"),
    "nuisance_regression_subject": ("registered_bold", "confound_matrix"),
    "reho_subject": ("filtered_bold",),
    "rsfmri_report_exporter": ("registered_report",),
    "rsfmri_report_package_validator": ("report_package",),
    "subject_qc": ("smoothed_bold",),
    "temporal_filtering_subject": ("residual_bold",),
    "data_readiness_check": ("project_configuration",),
    "bids_validation_check": ("rawdata",),
    "rsfmri_bold_reference_check": ("registered_bold", "bids_sidecar"),
    "rsfmri_motion_qc_plan": ("bold_reference_readiness_report", "motion_parameters"),
    "rsfmri_preprocessing_plan_stub": ("motion_qc_readiness_report",),
    "rsfmri_report_plan_stub": ("preprocessing_plan_stub",),
    "spm_realign_subject": ("BOLD NIfTI", "BOLD sidecar JSON"),
}


def _artifact_schema(values: list[str], *, output: bool) -> tuple[ArtifactContract, ...]:
    return tuple(
        ArtifactContract(
            artifact_type=str(value),
            required=False,
            reload_required=output,
        )
        for value in values
    )


_SUBJECT_RECOVERY_NODES = {
    "nuisance_regression_subject",
    "temporal_filtering_subject",
    "alff_falff_subject",
    "reho_subject",
    "functional_connectivity_subject",
}

_PROJECT_RECOVERY_NODES = {
    "contract_smoke",
    "data_inspection",
    "dataset_evaluation",
    "environment_check",
}


def _recovery_policy(node_id: str, *, external: bool) -> ContractRetryPolicy:
    if external or node_id not in (_SUBJECT_RECOVERY_NODES | _PROJECT_RECOVERY_NODES):
        return ContractRetryPolicy()
    subject_level = node_id in _SUBJECT_RECOVERY_NODES
    mutable = {
        "nuisance_regression_subject": (
            "model",
            "include_intercept",
            "include_linear_trend",
            "include_global_signal",
        ),
        "temporal_filtering_subject": ("low_hz", "high_hz", "tr", "fallback_tr"),
        "alff_falff_subject": ("low_hz", "high_hz", "tr", "fallback_tr"),
        "reho_subject": ("neighborhood", "use_gm_mask"),
        "functional_connectivity_subject": (
            "roi_count",
            "atlas_path",
            "labels_path",
            "generate_seed_map",
        ),
    }.get(node_id, ())
    return ContractRetryPolicy(
        retryable=True,
        retryable_error_classes=("NODE_FAILED", "TRANSIENT_IO", "RESOURCE_TEMPORARY"),
        non_retryable_error_classes=(
            "SAFETY_POLICY_BLOCKED",
            "VALIDATION_FAILED",
            "RAW_DATA_MUTATION_REQUESTED",
        ),
        max_attempts=1 if subject_level else 2,
        backoff_policy="fixed",
        backoff_seconds=1,
        requires_approval=True,
        supports_subject_subset=subject_level,
        supports_resume=False,
        mutable_parameters_for_recovery=mutable,
        required_pre_retry_validations=("ticket_binding", "output_collision"),
        required_post_retry_validations=("observation", "goal_evaluation"),
        max_lifecycle_recovery_attempts=2,
        max_node_attempts=1 if subject_level else 2,
        max_subject_node_attempts=1 if subject_level else 2,
        max_replans=1,
        max_recovery_wall_seconds=600,
    )


def _idempotency_policy(node_id: str) -> IdempotencyPolicy:
    safe_idempotent = node_id in {"contract_smoke", "environment_check"}
    isolated = node_id in (_SUBJECT_RECOVERY_NODES | _PROJECT_RECOVERY_NODES)
    return IdempotencyPolicy(
        idempotent=safe_idempotent,
        key_fields=("project_id", "plan_hash", "node_id"),
        overwrite_policy="fail_if_exists",
        output_collision_policy="isolated_attempt" if isolated else "fail_if_exists",
        attempt_output_strategy="isolated_subdirectory" if isolated else "none",
    )


_EXECUTABLE_PROJECT_LOW = frozenset(
    {
        "alff_falff_qc_dataset_report",
        "contract_smoke",
        "create_synthetic_bids",
        "data_inspection",
        "dataset_evaluation",
        "docs_inventory",
        "environment_check",
        "functional_connectivity_qc_dataset_report",
        "group_dataset_summary",
        "motion_qc_dataset_report",
        "normalization_qc_dataset_report",
        "nuisance_regression_qc_dataset_report",
        "project_release_readiness",
        "registration_qc_dataset_report",
        "reho_qc_dataset_report",
        "rsfmri_preprocessing_plan",
        "rsfmri_report_exporter",
        "rsfmri_report_package_validator",
        "slice_timing_qc_dataset_report",
        "smoothing_qc_dataset_report",
        "st_realign_motion_chain_report",
        "temporal_filtering_qc_dataset_report",
        "tissue_qc_dataset_report",
    }
)
_EXECUTABLE_SUBJECT_LOW = frozenset({"motion_qc_subject", "subject_qc"})
_EXECUTABLE_SUBJECT_MEDIUM = frozenset(
    {
        "alff_falff_subject",
        "functional_connectivity_subject",
        "nuisance_regression_subject",
        "reho_subject",
        "temporal_filtering_subject",
    }
)
_EXECUTABLE_NATIVE = frozenset(
    {
        "native_auto_acpc_align",
        "native_dicom_conversion_execute",
        "native_preproc_full_dry_run",
        "native_preproc_full_execute",
    }
)
_EXECUTABLE_IDS = (
    _EXECUTABLE_PROJECT_LOW
    | _EXECUTABLE_SUBJECT_LOW
    | _EXECUTABLE_SUBJECT_MEDIUM
    | _EXECUTABLE_NATIVE
)

_NATIVE_STAGE_IDS = frozenset(
    {
        "native_preproc_alff", "native_preproc_atlas_resampling",
        "native_preproc_bids_sidecar_validation", "native_preproc_coregistration",
        "native_preproc_detrending", "native_preproc_dicom_to_nifti",
        "native_preproc_dummy_scan_removal", "native_preproc_falff",
        "native_preproc_final_report", "native_preproc_functional_connectivity",
        "native_preproc_group_summary", "native_preproc_input_validation",
        "native_preproc_motion_qc", "native_preproc_normalization",
        "native_preproc_nuisance_regression", "native_preproc_realignment",
        "native_preproc_reho", "native_preproc_roi_timeseries",
        "native_preproc_segmentation", "native_preproc_slice_timing",
        "native_preproc_smoothing", "native_preproc_subject_qc",
        "native_preproc_temporal_filtering", "native_preproc_validation_report",
    }
)
_SPM_BLOCKED_IDS = frozenset(
    {
        "spm_coregister_subject", "spm_normalize_subject", "spm_realign_subject",
        "spm_segment_subject", "spm_slice_timing_subject", "spm_smoke_test",
        "spm_smooth_subject",
    }
)
_DPABI_BLOCKED_IDS = frozenset(
    {
        "dpabi_alff_falff_contract", "dpabi_capability_inspection",
        "dpabi_functional_connectivity_contract", "dpabi_input_manifest",
        "dpabi_nuisance_regression_contract", "dpabi_preflight", "dpabi_reho_contract",
        "dpabi_run_plan", "dpabi_sandbox_smoke_run", "dpabi_signature_probe",
        "dpabi_single_function_sandbox", "dpabi_subject_smooth",
        "dpabi_subject_wrapper_report", "dpabi_template_execute",
        "dpabi_template_instantiate", "dpabi_template_library",
        "dpabi_temporal_filtering_contract", "dpabi_wrapper_contracts",
        "dpabi_wrapper_scaffold", "dpabi_wrapper_validation_matrix",
    }
)
_GPU_BLOCKED_IDS = frozenset(
    {
        "alff_falff_gpu_candidate_contract", "functional_connectivity_gpu_candidate_contract",
        "gpu_alff_subject", "gpu_functional_connectivity_subject",
        "gpu_nuisance_regression_subject", "gpu_reho_subject", "gpu_synthetic_smoke",
        "gpu_temporal_filtering_subject", "reho_gpu_candidate_contract",
    }
)
_GPU_CONTRACT_IDS = frozenset(
    {
        "alff_falff_gpu_candidate_contract",
        "functional_connectivity_gpu_candidate_contract",
        "reho_gpu_candidate_contract",
    }
)
_PLAN_ONLY_IDS = frozenset(
    {
        "bids_validation_check", "data_readiness_check", "rsfmri_bold_reference_check",
        "rsfmri_motion_qc_plan", "rsfmri_preprocessing_plan_stub",
        "rsfmri_report_plan_stub",
    }
)

_REPORT_IDS = frozenset(
    node_id
    for node_id in _EXECUTABLE_PROJECT_LOW
    if node_id not in {"contract_smoke", "create_synthetic_bids", "data_inspection", "environment_check"}
)


def _write_roots(node_id: str) -> tuple[str, ...]:
    if node_id in _EXECUTABLE_SUBJECT_LOW | _EXECUTABLE_SUBJECT_MEDIUM:
        return ("derivatives",)
    if node_id == "rsfmri_report_exporter":
        return ("exports",)
    if node_id in _REPORT_IDS:
        return ("reports",)
    if node_id == "native_dicom_conversion_execute":
        return ("data", "work", "logs")
    if node_id == "native_auto_acpc_align":
        return ("derivatives", "work", "logs")
    if node_id == "native_preproc_full_execute":
        return ("derivatives", "reports", "work", "logs")
    if node_id == "native_preproc_full_dry_run":
        return ("work", "logs")
    if node_id in _NATIVE_STAGE_IDS:
        return ("derivatives", "reports")
    if node_id in _SPM_BLOCKED_IDS | _DPABI_BLOCKED_IDS | _GPU_BLOCKED_IDS:
        return ()
    return ("work", "logs") if node_id in _EXECUTABLE_IDS else ()


def _declared_contract_fields(node_id: str) -> dict[str, Any]:
    if node_id in _EXECUTABLE_IDS:
        backend = {
            "native_auto_acpc_align": "native_python",
            "native_dicom_conversion_execute": "medimage-native",
            "native_preproc_full_dry_run": "native_python",
            "native_preproc_full_execute": "native_python",
        }.get(node_id, "python")
        return {
            "backend": backend,
            "parallel_level": (
                "subject"
                if node_id in _EXECUTABLE_SUBJECT_LOW | _EXECUTABLE_SUBJECT_MEDIUM
                else "project"
            ),
            "requires_approval": node_id in {
                "native_auto_acpc_align", "native_dicom_conversion_execute",
                "native_preproc_full_execute",
            },
            "manual_required": False,
            "risk_level": (
                "medium"
                if node_id in _EXECUTABLE_SUBJECT_MEDIUM
                or node_id in {
                    "native_auto_acpc_align", "native_dicom_conversion_execute",
                    "native_preproc_full_execute",
                }
                else "low"
            ),
            "capability_level": (
                "metadata_only"
                if node_id in {"contract_smoke", "rsfmri_preprocessing_plan"}
                else "computed"
            ),
            "executable": True,
        }
    if node_id in _PLAN_ONLY_IDS:
        return {
            "backend": "contract", "parallel_level": "project",
            "requires_approval": False, "manual_required": False,
            "risk_level": "low", "capability_level": "metadata_only", "executable": False,
        }
    if node_id in _NATIVE_STAGE_IDS:
        return {
            "backend": "native_python", "parallel_level": "project",
            "requires_approval": False, "manual_required": False,
            "risk_level": "low", "capability_level": "computed", "executable": False,
        }
    if node_id in _SPM_BLOCKED_IDS:
        return {
            "backend": "matlab-spm", "parallel_level": "subject",
            "requires_approval": True,
            "manual_required": node_id == "spm_realign_subject",
            "risk_level": "high", "capability_level": "scaffolded", "executable": False,
        }
    if node_id in _DPABI_BLOCKED_IDS:
        return {
            "backend": "dpabi", "parallel_level": "project",
            "requires_approval": True, "manual_required": False,
            "risk_level": "high", "capability_level": "unavailable", "executable": False,
        }
    if node_id in _GPU_BLOCKED_IDS:
        if node_id in _GPU_CONTRACT_IDS:
            return {
                "backend": "contract", "parallel_level": "project",
                "requires_approval": False, "manual_required": False,
                "risk_level": "low", "capability_level": "unavailable",
                "executable": False,
            }
        computed = node_id in {
            "gpu_alff_subject", "gpu_functional_connectivity_subject",
            "gpu_nuisance_regression_subject", "gpu_reho_subject",
            "gpu_temporal_filtering_subject",
        }
        return {
            "backend": "gpu", "parallel_level": "subject",
            "requires_approval": True, "manual_required": False,
            "risk_level": "medium",
            "capability_level": "computed" if computed else "unavailable",
            "executable": False,
        }
    raise ValueError(f"Node id has no explicit contract declaration: {node_id}")


def _build_contracts() -> dict[str, NodeContract]:
    declared_ids = (
        _EXECUTABLE_IDS | _NATIVE_STAGE_IDS | _SPM_BLOCKED_IDS
        | _DPABI_BLOCKED_IDS | _GPU_BLOCKED_IDS | _PLAN_ONLY_IDS
    )
    expected_ids = set(NODE_REGISTRY) | set(_PLAN_ONLY_IDS)
    if declared_ids != expected_ids:
        missing = sorted(expected_ids - declared_ids)
        extra = sorted(declared_ids - expected_ids)
        raise ValueError(f"Node contract declarations inconsistent: missing={missing}, extra={extra}")
    if not _EXECUTABLE_IDS <= set(_STRICT_PARAMETERS):
        raise ValueError(
            "Executable nodes without explicit parameter schemas: "
            f"{sorted(_EXECUTABLE_IDS - set(_STRICT_PARAMETERS))}"
        )
    contracts: dict[str, NodeContract] = {}
    for node_id in sorted(declared_ids):
        if node_id in contracts:
            raise ValueError(f"Duplicate node contract id: {node_id}")
        fields = _declared_contract_fields(node_id)
        executable = bool(fields["executable"])
        external = fields["backend"] in {"matlab-spm", "dpabi", "gpu"}
        strict = node_id in _STRICT_PARAMETERS
        contracts[node_id] = NodeContract(
            node_id=node_id,
            contract_version=(
                "1.1.0"
                if node_id in {"native_preproc_full_execute", "native_dicom_conversion_execute"}
                else "1.0.0"
            ),
            backend=fields["backend"],
            parallel_level=fields["parallel_level"],
            requires_approval=fields["requires_approval"],
            manual_required=fields["manual_required"],
            risk_level=fields["risk_level"],
            write_roots=_write_roots(node_id),
            input_schema=_artifact_schema(
                list(_STRICT_INPUT_ARTIFACT_TYPES.get(node_id, ())), output=False
            ),
            parameter_schema=deepcopy(_STRICT_PARAMETERS.get(node_id, {})),
            output_schema=_artifact_schema(
                list(_STRICT_OUTPUT_ARTIFACT_TYPES.get(node_id, ())),
                output=True,
            ),
            preconditions=("reviewed plan validation succeeded",),
            postconditions=("declared artifacts and node state agree",),
            side_effects=("writes only within ticket output roots",),
            resources=ResourceRequirements(
                backend=fields["backend"],
                gpu_required=fields["backend"] == "gpu",
                process_mode=(
                    "sandbox_process"
                    if fields["backend"] in {"matlab-spm", "dpabi"}
                    else "in_process"
                ),
            ),
            retry_policy=_recovery_policy(node_id, external=external),
            idempotency=_idempotency_policy(node_id),
            validation_policy=ValidationPolicy(
                allow_additional_parameters=not strict and not executable,
                enforce_backend=True,
                compatibility_mode=None,
                deprecation=None,
            ),
            capability_level=fields["capability_level"],
            executable=executable,
        )
    return contracts


NODE_CONTRACTS: dict[str, NodeContract] = _build_contracts()


def get_node_contract(node_id: str) -> NodeContract:
    try:
        return NODE_CONTRACTS[node_id]
    except KeyError as exc:
        raise KeyError(f"No node contract registered for node id: {node_id}") from exc


def executable_contract_versions() -> dict[str, str]:
    return {
        node_id: contract.contract_version
        for node_id, contract in NODE_CONTRACTS.items()
        if contract.executable
    }


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return False


def validate_and_normalize_parameters(
    contract: NodeContract,
    params: dict[str, Any],
) -> tuple[dict[str, Any], ContractValidationEvidence | None, list[str]]:
    errors: list[str] = []
    normalized = deepcopy(params)
    unknown = sorted(set(params) - set(contract.parameter_schema))
    if unknown and not contract.validation_policy.allow_additional_parameters:
        errors.append(f"unknown parameters: {unknown}")
    for name, rule in contract.parameter_schema.items():
        if name not in normalized:
            if rule.required:
                errors.append(f"missing required parameter: {name}")
            elif rule.default is not None:
                normalized[name] = deepcopy(rule.default)
            continue
        value = normalized[name]
        if value is None and rule.nullable:
            continue
        if not _matches_type(value, rule.type):
            errors.append(f"parameter '{name}' must be {rule.type}")
            continue
        if rule.enum and value not in rule.enum:
            errors.append(f"parameter '{name}' must be one of {list(rule.enum)}")
        if isinstance(value, int | float) and not isinstance(value, bool):
            if rule.minimum is not None and value < rule.minimum:
                errors.append(f"parameter '{name}' must be >= {rule.minimum}")
            if rule.maximum is not None and value > rule.maximum:
                errors.append(f"parameter '{name}' must be <= {rule.maximum}")
        if isinstance(value, list):
            if rule.min_items is not None and len(value) < rule.min_items:
                errors.append(f"parameter '{name}' requires at least {rule.min_items} items")
            if rule.max_items is not None and len(value) > rule.max_items:
                errors.append(f"parameter '{name}' allows at most {rule.max_items} items")
    if contract.node_id in {"temporal_filtering_subject", "alff_falff_subject"}:
        low = normalized.get("low_hz")
        high = normalized.get("high_hz")
        if isinstance(low, int | float) and isinstance(high, int | float) and low >= high:
            errors.append("low_hz must be lower than high_hz")
    if contract.node_id in {"native_preproc_full_execute", "native_preproc_full_dry_run"}:
        if not any(
            str(normalized.get(name) or "").strip()
            for name in ("input_bold", "input_bids_dir", "conversion_run_id")
        ):
            errors.append("input_bold, input_bids_dir, or conversion_run_id is required")
    if errors:
        return normalized, None, errors
    evidence = ContractValidationEvidence(
        node_id=contract.node_id,
        contract_version=contract.contract_version,
        normalized_parameters=normalized,
        normalized_parameters_hash=stable_hash(normalized),
        checks=("parameter_schema", "backend", "contract_executable"),
    )
    return normalized, evidence, []
