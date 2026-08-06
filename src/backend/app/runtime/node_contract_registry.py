"""Stable node-id to versioned execution-contract registry."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_registry import NODE_REGISTRY
from src.backend.app.runtime.tool_catalog import TOOL_METADATA, build_tool_catalog
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
    },
    "native_preproc_full_dry_run": {
        "input_bold": _parameter("string", nullable=True, path_access="read"),
        "input_bids_dir": _parameter("string", nullable=True, path_access="read"),
        "conversion_run_id": _parameter("string", nullable=True, path_access="non_path"),
        "sidecar_json": _parameter("string", nullable=True, path_access="read"),
        "output_dir": _parameter("string", nullable=True, path_access="write"),
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


def _build_contracts() -> dict[str, NodeContract]:
    catalog = {item.id: item for item in build_tool_catalog()}
    contracts: dict[str, NodeContract] = {}
    for node_id in sorted(catalog):
        if node_id in contracts:
            raise ValueError(f"Duplicate node contract id: {node_id}")
        item = catalog[node_id]
        explicit_metadata = node_id in TOOL_METADATA
        registered = node_id in NODE_REGISTRY
        strict = node_id in _STRICT_PARAMETERS
        external = item.backend in {"matlab-spm", "dpabi", "gpu"}
        metadata_contract = (
            item.backend == "contract"
            or bool(set(item.tags) & {"contract", "metadata", "plan", "preflight", "capability"})
            or any(token in node_id for token in ("_plan", "_stub", "_metadata", "_manifest", "_signature", "_template"))
        )
        capability = (
            "unavailable"
            if not explicit_metadata
            else "metadata_only"
            if not registered
            else "scaffolded"
            if external and item.manual_required
            else "metadata_only"
            if metadata_contract
            else "computed"
        )
        # External/manual contracts remain reviewable compatibility metadata,
        # but they are not executable contracts until their runner-specific
        # inputs, outputs, gates, and side effects are explicitly modelled.
        coordinated_native_stage = (
            node_id.startswith("native_preproc_")
            and node_id not in {"native_preproc_full_execute", "native_preproc_full_dry_run"}
        )
        executable = (
            registered
            and explicit_metadata
            and not (external or item.manual_required or coordinated_native_stage)
        )
        contracts[node_id] = NodeContract(
            node_id=node_id,
            contract_version=(
                "1.1.0"
                if node_id in {"native_preproc_full_execute", "native_dicom_conversion_execute"}
                else "1.0.0"
                if strict
                else "0.9.0-legacy"
            ),
            backend=item.backend,
            input_schema=_artifact_schema(item.inputs, output=False),
            parameter_schema=deepcopy(_STRICT_PARAMETERS.get(node_id, {})),
            output_schema=_artifact_schema(
                list(_STRICT_OUTPUT_ARTIFACT_TYPES.get(node_id, tuple(item.outputs))),
                output=True,
            ),
            preconditions=("reviewed plan validation succeeded",),
            postconditions=("declared artifacts and node state agree",),
            side_effects=("writes only within ticket output roots",),
            resources=ResourceRequirements(
                backend=item.backend,
                gpu_required=item.backend == "gpu",
                external_process=item.backend in {"matlab-spm", "dpabi"},
            ),
            retry_policy=_recovery_policy(node_id, external=external),
            idempotency=_idempotency_policy(node_id),
            validation_policy=ValidationPolicy(
                allow_additional_parameters=not strict,
                enforce_backend=True,
                compatibility_mode=None if strict else "legacy_v1",
                deprecation=(
                    None
                    if strict
                    else "Legacy permissive parameters will require an explicit schema in contract v1."
                ),
            ),
            capability_level=capability,
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
