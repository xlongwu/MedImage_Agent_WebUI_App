"""Plan Adapter — convert reviewed plans to executor-compatible pipeline dicts.

This module bridges the gap between the candidate plans produced by the
LLM Planner / Plan Review Console and the pipeline dicts expected by
the Pipeline Executor (via load_pipeline_yaml / PipelineSpec).

It also classifies nodes by execution policy so that the gated execution
API can decide which nodes are safe to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Dataclass ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlanAdapterResult:
    """Result of adapting a reviewed plan for execution."""

    ok: bool
    pipeline: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "pipeline": self.pipeline,
            "errors": self.errors,
            "warnings": self.warnings,
            "policy": self.policy,
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _catalog_map() -> dict[str, Any]:
    from src.backend.app.runtime.tool_catalog import build_tool_catalog  # noqa: E402

    return {item.id: item for item in build_tool_catalog()}


# ── Core conversion ──────────────────────────────────────────────────────────


def reviewed_plan_to_pipeline_dict(
    plan: dict[str, Any],
    *,
    name: str | None = None,
    description: str | None = None,
    modality: str = "rsfmri",
    execution_backend: str = "reviewed-plan",
) -> dict[str, Any]:
    """Convert a reviewed plan dict to an executor-compatible pipeline dict.

    Raises ValueError on structural errors (duplicate ids, unknown deps).
    """
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a dictionary.")

    catalog = _catalog_map()
    errors: list[str] = []
    nodes_out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    pipeline_id = plan.get("pipeline_id", "reviewed_plan")
    plan_nodes = plan.get("nodes", []) or []

    # Collect node ids for dependency validation
    node_ids = set()
    for n in plan_nodes:
        nid = n.get("id")
        if nid:
            node_ids.add(nid)

    for i, node in enumerate(plan_nodes):
        if not isinstance(node, dict):
            raise ValueError(f"Node at index {i} must be a dictionary.")

        nid = node.get("id")
        if not nid:
            raise ValueError(f"Node at index {i} is missing an 'id'.")

        if nid in seen_ids:
            raise ValueError(f"Duplicate node id: {nid}.")
        seen_ids.add(nid)

        cat = catalog.get(nid)

        # Backend fill
        backend = node.get("backend")
        if not backend:
            if cat:
                backend = cat.backend
            else:
                errors.append(f"Node '{nid}' has no backend and is not in Tool Catalog.")
                backend = "unknown"

        # Dependencies
        deps = node.get("depends_on", []) or []
        if not isinstance(deps, list):
            raise ValueError(f"Node '{nid}' has non-list 'depends_on'.")
        for dep in deps:
            if dep not in node_ids:
                raise ValueError(f"Node '{nid}' depends on unknown node: {dep}.")

        # Params
        params = node.get("params", {}) or {}
        if not isinstance(params, dict):
            raise ValueError(f"Node '{nid}' has non-dict 'params'.")

        nodes_out.append(
            {
                "id": nid,
                "contract_version": node.get("contract_version"),
                "name": cat.name if cat else str(nid),
                "agent": "system",
                "backend": backend,
                "depends_on": list(deps),
                "params": dict(params),
                "parallel_level": cat.parallel_level if cat else "project",
                "gpu_supported": False,
                "cache": False,
                "inputs": [],
                "outputs": [],
            }
        )

    return {
        "pipeline_id": name or pipeline_id,
        "version": "0.1.0",
        "modality": modality,
        "description": description or "Pipeline converted from reviewed plan.",
        "execution": {
            "run_id": f"reviewed_{pipeline_id}",
            "stop_on_failure": True,
            "backend": execution_backend,
        },
        "nodes": nodes_out,
    }


# ── Node classification ──────────────────────────────────────────────────────


def _satisfies_sandbox_contract(node: dict[str, Any]) -> bool:
    """Check if a spm_realign_subject node satisfies the sandbox contract.

    Requires sandbox_mode=true and input_bold non-empty.
    """
    params = node.get("params", {}) or {}
    sandbox = params.get("sandbox_mode")
    input_bold = params.get("input_bold")
    if sandbox is not True:
        return False
    if not input_bold or not isinstance(input_bold, str) or not input_bold.strip():
        return False
    return True


def _is_safe_sandbox_input(input_bold: str, *, allow_derivative: bool = False) -> bool:
    """Check if an input_bold path is safe for sandbox execution.

    Allowed:
      - synthetic BIDS rawdata paths
      - safe derivatives paths (only when allow_derivative=True)

    Blocked:
      - arbitrary paths, real rawdata, path traversal
    """
    normalized = input_bold.replace("\\", "/")
    if ".." in normalized:
        return False
    is_synthetic = "examples/synthetic_bids/rawdata" in normalized
    is_safe_derivative = (
        allow_derivative and "derivatives" in normalized and "rsfmri_preproc" in normalized
    )
    return is_synthetic or is_safe_derivative


def _satisfies_coregister_sandbox(node: dict[str, Any]) -> bool:
    """Check sandbox declaration for spm_coregister_subject.

    Policy layer validates sandbox declaration only.
    Runner layer validates concrete T1w/mean-functional paths at runtime.
    """
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("subject_source") != "synthetic_bids":
        return False
    if params.get("reference_source") != "derivatives_mean_functional":
        return False
    return True


def _satisfies_slice_timing_sandbox(node: dict[str, Any]) -> bool:
    """Check if a spm_slice_timing_subject node satisfies the sandbox contract.

    Requires: sandbox_mode=true, input_bold non-empty, and path is safe.
    """
    params = node.get("params", {}) or {}
    sandbox = params.get("sandbox_mode")
    input_bold = params.get("input_bold")
    if sandbox is not True:
        return False
    if not input_bold or not isinstance(input_bold, str) or not input_bold.strip():
        return False
    allow_derivative = bool(params.get("allow_derivative_input"))
    return _is_safe_sandbox_input(str(input_bold), allow_derivative=allow_derivative)


def _satisfies_segment_sandbox(node: dict[str, Any]) -> bool:
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("anatomical_source") != "coregistered_t1w":
        return False
    if params.get("tpm_source") != "spm_default_tpm":
        return False
    return True


def _satisfies_normalize_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("deformation_source") != "segment_deformation_field":
        return False
    if params.get("functional_source") != "sandbox_derivatives":
        return False
    return True


_DPABI_METADATA_NODES = frozenset(
    [
        "dpabi_capability_inspection",
        "dpabi_input_manifest",
        "dpabi_preflight",
        "dpabi_run_plan",
        "dpabi_signature_probe",
        "dpabi_wrapper_contracts",
        "dpabi_wrapper_scaffold",
        "dpabi_alff_falff_contract",
        "dpabi_functional_connectivity_contract",
        "dpabi_nuisance_regression_contract",
        "dpabi_reho_contract",
        "dpabi_temporal_filtering_contract",
        "dpabi_template_library",
        "dpabi_template_instantiate",
        "dpabi_template_execute",
    ]
)


def _satisfies_wrapper_report_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("report_only") is not True:
        return False
    if params.get("report_source") != "dpabi_subject_smooth_outputs":
        return False
    if params.get("output_policy") != "reports_dir_dpabi_only":
        return False
    return True


def _satisfies_validation_matrix_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("validation_matrix_only") is not True:
        return False
    if params.get("matrix_source") != "dpabi_contracts_and_reports":
        return False
    if params.get("output_policy") != "reports_dir_dpabi_validation_matrix_only":
        return False
    return True


def _satisfies_subject_smooth_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("subject_level") is not True:
        return False
    if params.get("subject_source") != "synthetic_sandbox":
        return False
    if params.get("input_source") != "synthetic_sandbox_derivatives":
        return False
    if params.get("fwhm_policy") != "bounded_3d":
        return False
    if params.get("output_policy") != "derivatives_dir_scoped":
        return False
    return True


def _satisfies_single_function_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("single_function_only") is not True:
        return False
    if params.get("function_policy") != "allowlisted_contract_only":
        return False
    fn = params.get("function_name", "")
    if not isinstance(fn, str) or ";" in fn or "|" in fn or "&" in fn or "`" in fn:
        return False
    from src.backend.app.tools.dpabi_safety import ALLOWED_FUNCTIONS

    if fn not in ALLOWED_FUNCTIONS:
        return False
    return True


def _satisfies_dpabi_smoke_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("smoke_only") is not True:
        return False
    if params.get("input_source") != "synthetic_sandbox":
        return False
    return True


def _satisfies_smooth_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("normalized_source") != "normalize_outputs":
        return False
    if params.get("fwhm_policy") != "bounded_3d":
        return False
    return True


def _satisfies_gpu_nuisance_regression_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("subject_level") is not True:
        return False
    if params.get("input_source") != "scoped_functional_derivative":
        return False
    if params.get("confounds_source") != "scoped_confounds_derivative":
        return False
    if params.get("output_policy") != "derivatives_dir_scoped":
        return False
    if params.get("device_policy") != "guarded_auto_cpu_cuda0":
        return False
    if params.get("memory_policy") != "bounded_subject_gpu_512mb":
        return False
    if params.get("nuisance_policy") != "bounded_ols_confounds_only":
        return False
    return True


def _satisfies_gpu_functional_connectivity_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("subject_level") is not True:
        return False
    if params.get("input_source") != "scoped_functional_or_timeseries_derivative":
        return False
    if params.get("output_policy") != "derivatives_dir_scoped":
        return False
    if params.get("device_policy") != "guarded_auto_cpu_cuda0":
        return False
    if params.get("memory_policy") != "bounded_subject_gpu_512mb":
        return False
    if params.get("fc_policy") != "bounded_roi_pearson_only":
        return False
    return True


def _satisfies_gpu_temporal_filtering_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("subject_level") is not True:
        return False
    if params.get("input_source") != "scoped_functional_derivative":
        return False
    if params.get("output_policy") != "derivatives_dir_scoped":
        return False
    if params.get("device_policy") != "guarded_auto_cpu_cuda0":
        return False
    if params.get("memory_policy") != "bounded_subject_gpu_512mb":
        return False
    if params.get("temporal_filter_policy") != "bounded_bandpass_butterworth":
        return False
    return True


def _satisfies_gpu_reho_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("subject_level") is not True:
        return False
    if params.get("input_source") != "scoped_functional_derivative":
        return False
    if params.get("output_policy") != "derivatives_dir_scoped":
        return False
    if params.get("device_policy") != "guarded_auto_cpu_cuda0":
        return False
    if params.get("memory_policy") != "bounded_subject_gpu_512mb":
        return False
    if params.get("reho_policy") != "bounded_neighborhood":
        return False
    return True


def _satisfies_gpu_alff_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("subject_level") is not True:
        return False
    if params.get("input_source") != "scoped_functional_derivative":
        return False
    if params.get("output_policy") != "derivatives_dir_scoped":
        return False
    if params.get("device_policy") != "guarded_auto_cpu_cuda0":
        return False
    if params.get("memory_policy") != "bounded_subject_gpu_512mb":
        return False
    if params.get("alff_policy") != "bounded_tr_and_frequency_band":
        return False
    return True


def _satisfies_gpu_synthetic_smoke_sandbox(node):
    params = node.get("params", {}) or {}
    if params.get("sandbox_mode") is not True:
        return False
    if params.get("synthetic_smoke") is not True:
        return False
    if params.get("device_policy") != "guarded_auto_cpu_cuda0":
        return False
    if params.get("memory_policy") != "bounded_1e6_elements_256mb":
        return False
    if params.get("output_policy") != "reports_dir_gpu_smoke_only":
        return False
    return True


_NATIVE_FULL_CONFIRMATIONS = frozenset(
    {
        "confirm_reviewed_native_execution",
        "confirm_rawdata_readonly",
        "confirm_no_external_tools",
        "confirm_research_use_only",
        "confirm_no_clinical_use",
    }
)


def _safe_native_path(value: Any, *, required: bool = False, output: bool = False) -> bool:
    if value in {None, ""}:
        return not required
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        return not required
    if ".." in normalized or any(token in normalized for token in (";", "|", "&", "`")):
        return False
    lowered = normalized.lower()
    if "third_party" in lowered or lowered.endswith(".m"):
        return False
    if output and "/rawdata/" in f"/{lowered}/":
        return False
    return True


def _safe_native_identifier(value: Any, *, required: bool = False) -> bool:
    if value in {None, ""}:
        return not required
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    if not normalized:
        return not required
    return ".." not in normalized and all(
        token not in normalized for token in (";", "|", "&", "`", "/", "\\")
    )


def _satisfies_native_full_execute_contract(node: dict[str, Any]) -> bool:
    params = node.get("params", {}) or {}
    if not isinstance(params, dict):
        return False
    confirmations = params.get("confirmations") or {}
    if not isinstance(confirmations, dict):
        confirmations = {}
    for key in _NATIVE_FULL_CONFIRMATIONS:
        if confirmations.get(key) is not True and params.get(key) is not True:
            return False
    if not _safe_native_path(params.get("input_bold")):
        return False
    if not _safe_native_path(params.get("input_bids_dir")):
        return False
    if not _safe_native_identifier(params.get("conversion_run_id")):
        return False
    if (
        not str(params.get("input_bold") or "").strip()
        and not str(params.get("input_bids_dir") or "").strip()
        and not str(params.get("conversion_run_id") or "").strip()
    ):
        return False
    if not _safe_native_path(params.get("sidecar_json")):
        return False
    if not _safe_native_path(params.get("t1w")):
        return False
    if not _safe_native_path(params.get("template")):
        return False
    if not _safe_native_path(params.get("atlas")):
        return False
    if not _safe_native_path(params.get("output_dir"), output=True):
        return False
    return True


def _satisfies_native_full_dry_run_contract(node: dict[str, Any]) -> bool:
    params = node.get("params", {}) or {}
    if not isinstance(params, dict):
        return False
    return (
        _safe_native_path(params.get("input_bold"))
        and _safe_native_path(params.get("input_bids_dir"))
        and _safe_native_identifier(params.get("conversion_run_id"))
        and (
            str(params.get("input_bold") or "").strip()
            or str(params.get("input_bids_dir") or "").strip()
            or str(params.get("conversion_run_id") or "").strip()
        )
        and _safe_native_path(params.get("sidecar_json"))
        and _safe_native_path(params.get("output_dir"), output=True)
    )


def classify_plan_nodes(plan: dict[str, Any]) -> dict[str, list[str]]:
    """Classify every node in a reviewed plan by execution policy.

    Returns a dict with allowed_* and blocked_* lists for gated execution.
    """
    catalog = _catalog_map()
    result: dict[str, list[str]] = {
        "allowed_python_nodes": [],
        "allowed_gpu_nodes": [],
        "allowed_gpu_synthetic_smoke_nodes": [],  # M8-GPU-T006d
        "allowed_gpu_alff_sandbox_nodes": [],  # M8-GPU-T007f
        "allowed_gpu_reho_sandbox_nodes": [],  # M8-GPU-T008d
        "allowed_gpu_temporal_filtering_sandbox_nodes": [],  # M8-GPU-T009d
        "allowed_gpu_functional_connectivity_sandbox_nodes": [],  # M8-GPU-T010d
        "allowed_gpu_nuisance_regression_sandbox_nodes": [],  # M8-GPU-T011d
        "allowed_contract_nodes": [],
        "allowed_spm_smoke_nodes": [],  # M6-T004b
        "allowed_spm_realign_sandbox_nodes": [],  # M6-T005d
        "allowed_spm_slice_timing_sandbox_nodes": [],  # M6-T006d
        "allowed_spm_coregister_sandbox_nodes": [],  # M6-T007d
        "allowed_spm_segment_sandbox_nodes": [],  # M6-T008d
        "allowed_spm_normalize_sandbox_nodes": [],  # M6-T009d
        "allowed_spm_smooth_sandbox_nodes": [],  # M6-T010d
        "allowed_dpabi_metadata_nodes": [],  # M7-DPABI-T002b
        "allowed_dpabi_sandbox_smoke_nodes": [],  # M7-DPABI-T004d
        "allowed_dpabi_single_function_sandbox_nodes": [],  # M7-DPABI-T005d
        "allowed_dpabi_subject_smooth_sandbox_nodes": [],  # M7-DPABI-T006d
        "allowed_dpabi_subject_wrapper_report_nodes": [],  # M7-DPABI-T007d
        "allowed_dpabi_validation_matrix_nodes": [],  # M7-DPABI-T008d
        "allowed_native_preproc_nodes": [],
        "blocked_spm_nodes": [],
        "blocked_dpabi_execution_nodes": [],
        "blocked_native_preproc_nodes": [],
        "blocked_manual_required_nodes": [],
        "blocked_unknown_nodes": [],
        "blocked_uncataloged_nodes": [],
    }

    plan_nodes = plan.get("nodes", []) or []
    for node in plan_nodes:
        nid = node.get("id", "")
        if not nid:
            continue
        cat = catalog.get(nid)

        # Unknown / uncataloged
        if cat is None:
            result["blocked_unknown_nodes"].append(nid)
            continue
        if "uncataloged" in cat.tags:
            result["blocked_uncataloged_nodes"].append(nid)
            continue

        # Manual required
        if cat.manual_required:
            result["blocked_manual_required_nodes"].append(nid)
            continue

        # SPM — M6-T004b: smoke, M6-T005d: realign, M6-T006d: slice timing
        if nid == "spm_smoke_test":
            result["allowed_spm_smoke_nodes"].append(nid)
            continue
        if nid == "spm_realign_subject" and _satisfies_sandbox_contract(node):
            result["allowed_spm_realign_sandbox_nodes"].append(nid)
            continue
        if nid == "spm_slice_timing_subject" and _satisfies_slice_timing_sandbox(node):
            result["allowed_spm_slice_timing_sandbox_nodes"].append(nid)
            continue
        if nid == "spm_coregister_subject" and _satisfies_coregister_sandbox(node):
            result["allowed_spm_coregister_sandbox_nodes"].append(nid)
            continue
        if nid == "spm_segment_subject" and _satisfies_segment_sandbox(node):
            result["allowed_spm_segment_sandbox_nodes"].append(nid)
            continue
        if nid == "spm_normalize_subject" and _satisfies_normalize_sandbox(node):
            result["allowed_spm_normalize_sandbox_nodes"].append(nid)
            continue
        if nid == "spm_smooth_subject" and _satisfies_smooth_sandbox(node):
            result["allowed_spm_smooth_sandbox_nodes"].append(nid)
            continue
        if nid.startswith("spm_") or cat.backend == "matlab-spm":
            result["blocked_spm_nodes"].append(nid)
            continue

        # DPABI — M7-T002b: metadata allowed, M7-T004d: sandbox smoke allowed (declaration-gated)
        if nid in _DPABI_METADATA_NODES:
            result["allowed_dpabi_metadata_nodes"].append(nid)
            continue
        if nid == "dpabi_sandbox_smoke_run" and _satisfies_dpabi_smoke_sandbox(node):
            result["allowed_dpabi_sandbox_smoke_nodes"].append(nid)
            continue
        if nid == "dpabi_single_function_sandbox" and _satisfies_single_function_sandbox(node):
            result["allowed_dpabi_single_function_sandbox_nodes"].append(nid)
            continue
        if nid == "dpabi_subject_smooth" and _satisfies_subject_smooth_sandbox(node):
            result["allowed_dpabi_subject_smooth_sandbox_nodes"].append(nid)
            continue
        if nid == "dpabi_subject_wrapper_report" and _satisfies_wrapper_report_sandbox(node):
            result["allowed_dpabi_subject_wrapper_report_nodes"].append(nid)
            continue
        if nid == "dpabi_wrapper_validation_matrix" and _satisfies_validation_matrix_sandbox(node):
            result["allowed_dpabi_validation_matrix_nodes"].append(nid)
            continue
        if nid.startswith("dpabi_") and not (
            "contract" in nid
            or "capability" in nid
            or "preflight" in nid
            or "scaffold" in nid
            or "signature" in nid
            or "template" in nid
            or "manifest" in nid
            or "run_plan" in nid
        ):
            result["blocked_dpabi_execution_nodes"].append(nid)
            continue

        # Native full preprocessing: cataloged, audited, and explicitly confirmed.
        if nid == "native_preproc_full_dry_run":
            if _satisfies_native_full_dry_run_contract(node):
                result["allowed_native_preproc_nodes"].append(nid)
            else:
                result["blocked_native_preproc_nodes"].append(nid)
            continue
        if nid == "native_preproc_full_execute":
            if _satisfies_native_full_execute_contract(node):
                result["allowed_native_preproc_nodes"].append(nid)
            else:
                result["blocked_native_preproc_nodes"].append(nid)
            continue
        if nid.startswith("native_preproc_"):
            result["blocked_native_preproc_nodes"].append(nid)
            continue

        # GPU synthetic smoke — M8-T006d: sandbox-gated allowlist
        if nid == "gpu_nuisance_regression_subject" and _satisfies_gpu_nuisance_regression_sandbox(
            node
        ):
            result["allowed_gpu_nuisance_regression_sandbox_nodes"].append(nid)
            continue
        if (
            nid == "gpu_functional_connectivity_subject"
            and _satisfies_gpu_functional_connectivity_sandbox(node)
        ):
            result["allowed_gpu_functional_connectivity_sandbox_nodes"].append(nid)
            continue
        if nid == "gpu_temporal_filtering_subject" and _satisfies_gpu_temporal_filtering_sandbox(
            node
        ):
            result["allowed_gpu_temporal_filtering_sandbox_nodes"].append(nid)
            continue
        if nid == "gpu_reho_subject" and _satisfies_gpu_reho_sandbox(node):
            result["allowed_gpu_reho_sandbox_nodes"].append(nid)
            continue
        if nid == "gpu_alff_subject" and _satisfies_gpu_alff_sandbox(node):
            result["allowed_gpu_alff_sandbox_nodes"].append(nid)
            continue
        if nid == "gpu_synthetic_smoke" and _satisfies_gpu_synthetic_smoke_sandbox(node):
            result["allowed_gpu_synthetic_smoke_nodes"].append(nid)
            continue

        # Allowed categories
        if cat.backend == "gpu":
            result["allowed_gpu_nodes"].append(nid)
        elif cat.backend == "contract":
            result["allowed_contract_nodes"].append(nid)
        else:
            result["allowed_python_nodes"].append(nid)

    return result


# ── Convenience ──────────────────────────────────────────────────────────────


def adapt_reviewed_plan(
    plan: dict[str, Any],
    *,
    name: str | None = None,
    description: str | None = None,
) -> PlanAdapterResult:
    """Full adaptation: convert + classify in one call."""
    errors: list[str] = []
    warnings: list[str] = []
    pipeline: dict[str, Any] | None = None
    policy: dict[str, list[str]] = {}

    # Classify
    try:
        policy = classify_plan_nodes(plan)
    except Exception as exc:
        errors.append(f"Node classification failed: {exc}")

    # Check for blocked nodes
    blocked = (
        policy.get("blocked_spm_nodes", [])
        + policy.get("blocked_dpabi_execution_nodes", [])
        + policy.get("blocked_native_preproc_nodes", [])
        + policy.get("blocked_manual_required_nodes", [])
        + policy.get("blocked_unknown_nodes", [])
        + policy.get("blocked_uncataloged_nodes", [])
    )
    if blocked:
        message = f"Plan contains {len(blocked)} blocked node(s): {', '.join(blocked)}"
        warnings.append(message)
        errors.append(message)

    # Convert
    try:
        pipeline = reviewed_plan_to_pipeline_dict(
            plan,
            name=name,
            description=description,
        )
    except ValueError as exc:
        errors.append(str(exc))

    return PlanAdapterResult(
        ok=len(errors) == 0,
        pipeline=pipeline,
        errors=errors,
        warnings=warnings,
        policy=policy,
    )
