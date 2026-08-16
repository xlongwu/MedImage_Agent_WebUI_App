"""Execute Reviewed Plan API (POST /api/plans/execute-reviewed).

Supports two modes:

  dry_run=true  — readiness check: validation, approval gate, adapter,
                   policy, optional pipeline YAML write, optional audit.
  dry_run=false — safe execution preflight (M5-T015) + gated execution
                   for safe allowlist nodes only (M5-T016).

ALL executor calls are gated behind env var, confirm_execution,
persist_audit, ProjectSettings, validation, approval, adapter,
execution policy, pipeline YAML write, audit, AND safe allowlist.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from src.backend.app.config.settings import ProjectSettings
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner import pipeline_writer  # imported as module for monkeypatch
from src.backend.app.planner.approval_gate import check_approval_gate
from src.backend.app.planner.audit_record import (
    audit_record_to_dict,
    build_review_audit_record,
    stable_hash,
    write_audit_record,
)
from src.backend.app.planner.plan_adapter import adapt_reviewed_plan
from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.planner.project_context import (
    ProjectContext,
    ProjectContextError,
    load_project_context,
    validate_plan_project_context,
)
from src.backend.app.planner.reviewed_plan_store import (
    ReviewedPlanStoreError,
    build_run_link,
    new_run_identity,
    resolve_reviewed_plan_for_execution,
)
from src.backend.app.runtime.execution_gateway import (
    ExecutionGateway,
    current_allowlist_hash,
)
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.schemas.desktop import ReviewedPlanRecord
from src.backend.app.schemas.approval_summary import ApprovalSummary
from src.backend.app.schemas.execution_consistency import (
    ExecutionConsistencyInput,
    verify_execution_consistency,
)
from src.backend.app.schemas.native_preproc_api import NativeFullPreprocRequest
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.mock_store import mock_store
from src.backend.app.services.native_preproc_full import run_native_full_dry_run
from src.backend.app.services.native_preproc_request import build_native_full_request
from src.backend.app.services.reviewed_execution_service import ReviewedExecutionService

router = APIRouter()

AUDIT_RECORD_DIR = Path("outputs/reports/audit_records")


class ExecuteReviewedRequest(BaseModel):
    plan: dict[str, Any]
    approval: dict[str, Any] | None = None
    project_id: str | None = None
    reviewed_plan_id: str | None = None
    project_config_path: str | None = None
    dry_run: bool = True
    persist_audit: bool = False
    write_pipeline_yaml: bool = False
    confirm_execution: bool = False
    actor: str | None = None
    lifecycle_id: str | None = None
    command_id: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pipeline_yaml_default() -> dict[str, Any]:
    return {
        "would_write": False,
        "written": False,
        "path": None,
        "requires_audit": True,
    }


def _pipeline_yaml_summary(
    *,
    would_write: bool = False,
    written: bool = False,
    path: str | None = None,
    requires_audit: bool = True,
) -> dict[str, Any]:
    return {
        "would_write": would_write,
        "written": written,
        "path": path,
        "requires_audit": requires_audit,
    }


def _plan_summary(plan: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    nodes = plan.get("nodes", []) or []
    return {
        "pipeline_id": plan.get("pipeline_id", "unknown"),
        "nodes_total": len(nodes),
        "approval_required_nodes": validation.get("approval_required_nodes", []),
        "high_risk_nodes": validation.get("high_risk_nodes", []),
    }


def _execution_meta(
    submitted: bool = False,
    run_id: str | None = None,
    executor_called: bool = False,
) -> dict[str, Any]:
    return {
        "submitted": submitted,
        "run_id": run_id,
        "executor_called": executor_called,
    }


def _no_audit() -> dict[str, Any]:
    return {"persisted": False}


def _adapter_summary(adapter_result: Any) -> dict[str, Any]:
    if adapter_result is None:
        return {
            "ok": False,
            "errors": [],
            "warnings": [],
            "policy": {},
            "pipeline": {"available": False},
        }
    pipeline = adapter_result.pipeline
    return {
        "ok": adapter_result.ok,
        "errors": adapter_result.errors,
        "warnings": adapter_result.warnings,
        "policy": adapter_result.policy,
        "pipeline": {
            "available": adapter_result.ok and pipeline is not None,
            "name": pipeline.get("pipeline_id", "unknown") if pipeline else "unknown",
            "nodes_total": len(pipeline.get("nodes", [])) if pipeline else 0,
            "modality": pipeline.get("modality", "rsfmri") if pipeline else "rsfmri",
        }
        if adapter_result.ok and pipeline
        else {"available": False},
    }


def _is_policy_blocked(policy: dict[str, list[str]]) -> bool:
    blocked = (
        policy.get("blocked_spm_nodes", [])
        + policy.get("blocked_dpabi_execution_nodes", [])
        + policy.get("blocked_manual_required_nodes", [])
        + policy.get("blocked_unknown_nodes", [])
        + policy.get("blocked_uncataloged_nodes", [])
    )
    return len(blocked) > 0


def _check_safe_allowlist(policy: dict[str, list[str]]) -> str | None:
    """Check that all allowed nodes are in the safe allowlist.

    Returns error status string if any node is not in the allowlist, else None.

    M5: pure-Python nodes only.
    M6-T004b: also allows spm_smoke_test (verified MATLAB/SPM environment smoke).
    """
    gpu_nodes = policy.get("allowed_gpu_nodes", [])
    contract_nodes = policy.get("allowed_contract_nodes", [])
    spm_smoke_nodes = policy.get("allowed_spm_smoke_nodes", [])
    spm_realign_sandbox_nodes = policy.get("allowed_spm_realign_sandbox_nodes", [])
    spm_slice_timing_sandbox_nodes = policy.get("allowed_spm_slice_timing_sandbox_nodes", [])
    spm_coregister_sandbox_nodes = policy.get("allowed_spm_coregister_sandbox_nodes", [])
    spm_segment_sandbox_nodes = policy.get("allowed_spm_segment_sandbox_nodes", [])
    spm_normalize_sandbox_nodes = policy.get("allowed_spm_normalize_sandbox_nodes", [])
    spm_smooth_sandbox_nodes = policy.get("allowed_spm_smooth_sandbox_nodes", [])
    dpabi_metadata_nodes = policy.get("allowed_dpabi_metadata_nodes", [])
    dpabi_sandbox_smoke_nodes = policy.get("allowed_dpabi_sandbox_smoke_nodes", [])
    dpabi_single_function_sandbox_nodes = policy.get(
        "allowed_dpabi_single_function_sandbox_nodes", []
    )
    dpabi_subject_smooth_sandbox_nodes = policy.get(
        "allowed_dpabi_subject_smooth_sandbox_nodes", []
    )
    dpabi_subject_wrapper_report_nodes = policy.get(
        "allowed_dpabi_subject_wrapper_report_nodes", []
    )
    dpabi_validation_matrix_nodes = policy.get("allowed_dpabi_validation_matrix_nodes", [])

    contract_nodes = policy.get("allowed_contract_nodes", [])
    gpu_synthetic_smoke_nodes = policy.get("allowed_gpu_synthetic_smoke_nodes", [])
    gpu_alff_sandbox_nodes = policy.get("allowed_gpu_alff_sandbox_nodes", [])
    gpu_reho_sandbox_nodes = policy.get("allowed_gpu_reho_sandbox_nodes", [])
    gpu_temporal_filtering_sandbox_nodes = policy.get(
        "allowed_gpu_temporal_filtering_sandbox_nodes", []
    )
    gpu_functional_connectivity_sandbox_nodes = policy.get(
        "allowed_gpu_functional_connectivity_sandbox_nodes", []
    )
    gpu_nuisance_regression_sandbox_nodes = policy.get(
        "allowed_gpu_nuisance_regression_sandbox_nodes", []
    )
    native_preproc_nodes = policy.get("allowed_native_preproc_nodes", [])
    unsafe = contract_nodes + gpu_nodes
    if unsafe:
        return "SAFE_EXECUTION_POLICY_BLOCKED"

    # Must have at least one allowed node
    python_nodes = policy.get("allowed_python_nodes", [])
    total_allowed = (
        python_nodes
        + spm_smoke_nodes
        + spm_realign_sandbox_nodes
        + spm_slice_timing_sandbox_nodes
        + spm_coregister_sandbox_nodes
        + spm_segment_sandbox_nodes
        + spm_normalize_sandbox_nodes
        + spm_smooth_sandbox_nodes
        + dpabi_metadata_nodes
        + dpabi_sandbox_smoke_nodes
        + dpabi_single_function_sandbox_nodes
        + dpabi_subject_smooth_sandbox_nodes
        + dpabi_subject_wrapper_report_nodes
        + dpabi_validation_matrix_nodes
        + contract_nodes
        + gpu_synthetic_smoke_nodes
        + gpu_alff_sandbox_nodes
        + gpu_reho_sandbox_nodes
        + gpu_temporal_filtering_sandbox_nodes
        + gpu_functional_connectivity_sandbox_nodes
        + gpu_nuisance_regression_sandbox_nodes
        + native_preproc_nodes
    )
    if not total_allowed:
        return "SAFE_EXECUTION_POLICY_BLOCKED"
    return None


def _write_audit(
    event_type: str,
    plan: dict[str, Any],
    validation: dict[str, Any],
    approval: dict[str, Any] | None,
    gate: dict[str, Any] | None,
    dry_run_result: dict[str, Any] | None,
    actor: str | None,
    request: ExecuteReviewedRequest,
) -> dict[str, Any]:
    if not request.persist_audit:
        return _no_audit()
    try:
        record = build_review_audit_record(
            event_type=event_type,
            plan=plan,
            validation=validation,
            approval=approval,
            approval_gate=gate,
            dry_run_result=dry_run_result,
            actor=actor or request.actor,
            source="execute_reviewed_api",
        )
        path = write_audit_record(record, AUDIT_RECORD_DIR)
        result = {
            "persisted": True,
            "audit_id": record.audit_id,
            "audit_path": str(path),
            "event_type": event_type,
        }
        if request.project_id:
            project = mock_store.get_project(request.project_id)
            metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
            project_dir = metadata.get("project_dir")
            if project_dir:
                project_root = Path(str(project_dir)).expanduser().resolve()
                projection_path = (
                    project_root / "reports" / "audit_records" / f"{record.audit_id}.json"
                ).resolve()
                try:
                    projection_path.relative_to(project_root)
                    atomic_write_json(
                        projection_path,
                        audit_record_to_dict(record),
                        schema_version=1,
                    )
                    result["project_audit_path"] = str(projection_path)
                except Exception as exc:
                    result["projection_warning"] = f"PROJECT_AUDIT_PROJECTION_FAILED: {exc}"
        return result
    except Exception:
        return {"persisted": False, "error": "Failed to write audit record"}


def _blocked_result(
    status: str,
    plan: dict[str, Any],
    validation: dict[str, Any],
    gate: dict[str, Any] | None,
    adapter: Any,
    request: ExecuteReviewedRequest,
) -> dict[str, Any]:
    result = {
        "ok": False,
        "status": status,
        "dry_run": request.dry_run,
        "would_execute": False,
        "execution_allowed": False,
        "validation": validation,
        "approval_gate": gate,
        "adapter": _adapter_summary(adapter),
        "pipeline_yaml": _pipeline_yaml_default(),
        "project_config_path": request.project_config_path,
        "execution": _execution_meta(),
    }
    result["audit"] = _write_audit(
        "execution_blocked",
        plan,
        validation,
        request.approval,
        gate,
        result,
        request.actor,
        request,
    )
    return result


@router.post("/api/plans/execute-reviewed")
def api_execute_reviewed(request: ExecuteReviewedRequest) -> dict[str, Any]:
    """Compatibility HTTP adapter for the shared reviewed execution service."""
    return ReviewedExecutionService().execute(request)


def _early_blocked(
    status: str,
    request: ExecuteReviewedRequest,
) -> dict[str, Any]:
    """Return a blocked result before any validation/approval/adapter runs."""
    return {
        "ok": False,
        "status": status,
        "dry_run": request.dry_run,
        "would_execute": False,
        "execution_allowed": False,
        "validation": None,
        "approval_gate": None,
        "adapter": None,
        "pipeline_yaml": _pipeline_yaml_default(),
        "plan_summary": None,
        "project_config_path": request.project_config_path,
        "execution": _execution_meta(),
        "audit": _no_audit(),
    }


def _try_write_pipeline_yaml(
    adapter: Any,
    request: ExecuteReviewedRequest,
    plan_hash: str | None = None,
) -> tuple[dict[str, Any], str | None, Path | None]:
    if not request.write_pipeline_yaml:
        return _pipeline_yaml_summary(would_write=True, written=False), None, None

    if not request.persist_audit:
        return (
            _pipeline_yaml_summary(
                would_write=True,
                written=False,
                requires_audit=True,
            ),
            "PIPELINE_WRITE_REQUIRES_AUDIT",
            None,
        )

    try:
        pipeline_dict = adapter.pipeline if adapter else None
        if pipeline_dict is None:
            return (
                _pipeline_yaml_summary(
                    would_write=True,
                    written=False,
                ),
                "PIPELINE_WRITE_FAILED",
                None,
            )
        path = pipeline_writer.write_reviewed_pipeline_yaml(
            pipeline_dict,
            plan_hash=plan_hash,
        )
        return (
            _pipeline_yaml_summary(
                would_write=True,
                written=True,
                path=str(path),
            ),
            None,
            path,
        )
    except Exception:
        return (
            _pipeline_yaml_summary(
                would_write=True,
                written=False,
            ),
            "PIPELINE_WRITE_FAILED",
            None,
        )


def _validate_project_config(project_config_path: str | None) -> tuple[Any, str | None]:
    if not project_config_path:
        return None, "PROJECT_CONFIG_REQUIRED"
    try:
        settings = ProjectSettings.from_yaml(project_config_path)
        return settings, None
    except FileNotFoundError:
        return None, "PROJECT_CONFIG_INVALID"
    except Exception:
        return None, "PROJECT_CONFIG_INVALID"


def _check_project_context(
    plan: dict[str, Any],
    project_config_path: str,
    project_id: str | None = None,
) -> tuple[ProjectContext | None, str | None, list[str]]:
    try:
        context = load_project_context(
            project_id=project_id,
            project_config_path=project_config_path,
        )
    except ProjectContextError as exc:
        return None, "PROJECT_CONTEXT_INVALID", [str(exc)]

    errors = validate_plan_project_context(plan, context)
    if errors:
        return context, "PROJECT_CONTEXT_MISMATCH", errors
    return context, None, []


def _project_context_blocked(
    status: str,
    request: ExecuteReviewedRequest,
    errors: list[str],
    context: ProjectContext | None = None,
) -> dict[str, Any]:
    result = _early_blocked(status, request)
    result["errors"] = errors
    result["project_context"] = context.to_dict() if context else None
    return result


def _is_preflight_enabled() -> bool:
    return os.environ.get("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "") == "1"


def _link_fields(
    *,
    reviewed_plan_id: str | None = None,
    run_link_id: str | None = None,
    run_id: str | None = None,
    pipeline_path: str | None = None,
    summary_path: str | None = None,
) -> dict[str, Any]:
    return {
        "reviewed_plan_id": reviewed_plan_id,
        "run_link_id": run_link_id,
        "run_id": run_id,
        "pipeline_path": pipeline_path,
        "summary_path": summary_path,
    }


def _with_link_fields(result: dict[str, Any], **fields: str | None) -> dict[str, Any]:
    result.update(_link_fields(**fields))
    return result


def _reviewed_plan_error_status(exc: ReviewedPlanStoreError) -> str:
    code = str(exc).partition(":")[0].strip()
    return code if code.startswith("REVIEWED_PLAN_") else "REVIEWED_PLAN_INVALID"


def _native_preproc_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = plan.get("nodes", []) or []
    return [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("id") == "native_preproc_full_execute"
    ]


def _native_preproc_request_from_node(
    node: dict[str, Any],
    *,
    fallback_run_id: str = "",
) -> NativeFullPreprocRequest:
    return build_native_full_request(
        node.get("params") if isinstance(node.get("params"), dict) else {},
        fallback_run_id=fallback_run_id,
    )


def _native_project_metadata(
    *,
    project_id: str,
    context: ProjectContext | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if project_id:
        project = mock_store.get_project(project_id)
        if project is not None and isinstance(project.metadata, dict):
            metadata.update(project.metadata)
    if context is not None:
        if context.project_dir is not None:
            metadata.setdefault("project_dir", str(context.project_dir))
        for key, value in context.diagnostics.items():
            metadata.setdefault(key, value)
    return metadata


def _native_project_id(
    plan: dict[str, Any],
    request: ExecuteReviewedRequest,
    context: ProjectContext | None,
    params: dict[str, Any],
) -> str:
    project_context = plan.get("project_context")
    return str(
        params.get("project_id")
        or request.project_id
        or (context.project_id if context else "")
        or (project_context.get("project_id") if isinstance(project_context, dict) else "")
        or ""
    )


def _native_project_dir(
    context: ProjectContext | None,
    params: dict[str, Any],
    metadata: dict[str, object],
) -> str:
    return str(
        params.get("project_dir")
        or metadata.get("project_dir")
        or (context.project_dir if context and context.project_dir else "")
        or ""
    )


def _native_readiness_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for issue in payload.get("blocking_issues") or []:
        errors.append(str(issue))
    for stage in payload.get("stage_results") or []:
        if not isinstance(stage, dict) or stage.get("status") != "blocked":
            continue
        stage_id = str(stage.get("stage_id") or "native_stage")
        for issue in stage.get("blocking_issues") or []:
            errors.append(f"{stage_id}: {issue}")
    return errors


def _check_native_preproc_readiness(
    plan: dict[str, Any],
    request: ExecuteReviewedRequest,
    context: ProjectContext | None,
) -> dict[str, Any] | None:
    native_nodes = _native_preproc_nodes(plan)
    if not native_nodes:
        return None

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, node in enumerate(native_nodes, start=1):
        params = dict(node.get("params") or {})
        project_id = _native_project_id(plan, request, context, params)
        metadata = _native_project_metadata(project_id=project_id, context=context)
        project_dir = _native_project_dir(context, params, metadata)
        native_request = _native_preproc_request_from_node(
            node,
            fallback_run_id=f"preflight-native-{index}",
        )
        registry_path = Path(str(metadata.get("preprocessing_input_registry_path") or ""))
        registered_run = str(metadata.get("preprocessing_conversion_run_id") or "")
        needs_conversion = bool(
            native_request.conversion_run_id
            and not (registry_path.is_file() and registered_run == native_request.conversion_run_id)
        )
        if needs_conversion:
            from src.backend.app.services.dicom_conversion_execution import (
                run_internal_user_dicom_conversion_from_persisted_package,
            )

            rawdata_dir = str(
                metadata.get("rawdata_dir")
                or (context.rawdata_dir if context and context.rawdata_dir else "")
                or ""
            )
            conversion_readiness = run_internal_user_dicom_conversion_from_persisted_package(
                project_id,
                native_request.conversion_run_id,
                project_dir=project_dir,
                rawdata_dir=rawdata_dir,
                validate_only=True,
                input_roots=tuple(value for value in (project_dir, rawdata_dir) if value),
                output_roots=(project_dir,) if project_dir else (),
                readonly_roots=(rawdata_dir,) if rawdata_dir else (),
            )
            payload = conversion_readiness.model_dump(mode="json")
            payload["node_id"] = node.get("id")
            payload["readiness_scope"] = "reviewed_native_conversion_handoff"
            results.append(payload)
            if not conversion_readiness.ok:
                errors.extend(conversion_readiness.blocking_issues)
                errors.extend(conversion_readiness.errors)
            else:
                overrides = dict(native_request.stage_overrides or {})

                def _resource_available(
                    explicit: str,
                    folder: str,
                    project_root: str = project_dir,
                ) -> bool:
                    if explicit and Path(explicit).is_file():
                        return True
                    resource_dir = Path(project_root) / "resources" / folder
                    return resource_dir.is_dir() and any(
                        path.is_file() and path.name.lower().endswith((".nii", ".nii.gz"))
                        for path in resource_dir.iterdir()
                    )

                if overrides.get("normalization", True) is not False and not _resource_available(
                    native_request.template,
                    "templates",
                ):
                    errors.append(
                        "normalization: a project-owned template is required or the stage must be explicitly disabled."
                    )
                atlas_stages = (
                    "atlas_resampling",
                    "roi_timeseries",
                    "functional_connectivity",
                )
                if any(
                    overrides.get(stage, True) is not False for stage in atlas_stages
                ) and not _resource_available(
                    native_request.atlas,
                    "atlases",
                ):
                    errors.append(
                        "functional connectivity: a project-owned atlas is required or all atlas-dependent stages must be explicitly disabled."
                    )
            continue
        response = run_native_full_dry_run(
            project_id,
            native_request,
            project_dir=project_dir,
            project_metadata=metadata,
            persist_artifacts=False,
        )
        payload = response.model_dump(mode="json")
        payload["node_id"] = node.get("id")
        results.append(payload)
        if not response.ok:
            errors.extend(_native_readiness_errors(payload))

    if not errors:
        return {
            "ok": True,
            "status": "NATIVE_PREPROC_READINESS_OK",
            "results": results,
        }
    return {
        "ok": False,
        "status": "NATIVE_PREPROC_READINESS_BLOCKED",
        "results": results,
        "errors": errors,
    }


def _native_preproc_readiness_blocked_result(
    *,
    plan: dict[str, Any],
    validation: dict[str, Any],
    gate: dict[str, Any],
    adapter: Any,
    request: ExecuteReviewedRequest,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "ok": False,
        "status": "NATIVE_PREPROC_READINESS_BLOCKED",
        "dry_run": request.dry_run,
        "would_execute": False,
        "execution_allowed": False,
        "validation": validation,
        "approval_gate": gate,
        "adapter": _adapter_summary(adapter),
        "pipeline_yaml": _pipeline_yaml_default(),
        "plan_summary": _plan_summary(plan, validation),
        "project_config_path": request.project_config_path,
        "execution": _execution_meta(),
        "native_preproc_readiness": readiness,
        "errors": readiness.get("errors", []),
    }
    result["audit"] = _write_audit(
        "execution_blocked",
        plan,
        validation,
        request.approval,
        gate,
        result,
        request.actor,
        request,
    )
    return result


# ── Consistency preflight (Phase 3) ──────────────────────────────────────────


def _run_consistency_preflight(
    *,
    plan: dict[str, Any],
    reviewed_plan: ReviewedPlanRecord | None,
    request: ExecuteReviewedRequest,
    adapter: Any,
    audit_info: dict[str, Any],
    project_id: str,
) -> dict[str, Any] | None:
    """Run dry-run/execute consistency check before calling run_pipeline().

    Returns None if consistency passes (or is not required).
    Returns a blocked result dict on hard consistency failure.
    """
    node_ids = [n["id"] for n in plan.get("nodes", [])]

    reviewed_input = ExecutionConsistencyInput(
        project_id=project_id,
        reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
        plan_hash=reviewed_plan.plan_hash if reviewed_plan else None,
        project_config_path=reviewed_plan.project_config_path
        if reviewed_plan
        else request.project_config_path,
        project_context_path=reviewed_plan.project_config_path
        if reviewed_plan
        else request.project_config_path,
        node_ids=node_ids,
    )

    dry_run_input = ExecutionConsistencyInput(
        project_id=project_id,
        reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
        node_ids=node_ids,
        dry_run_status="EXECUTION_PREFLIGHT_READY",
    )

    execution_input = ExecutionConsistencyInput(
        project_id=project_id,
        reviewed_plan_id=request.reviewed_plan_id,
        project_config_path=request.project_config_path,
        project_context_path=request.project_config_path,
        node_ids=node_ids,
        approval_summary_hash="preflight_approved",  # approval gate already passed
        audit_id=str(audit_info.get("audit_id") or "") or None,
    )

    report = verify_execution_consistency(
        reviewed=reviewed_input,
        dry_run=dry_run_input,
        execution=execution_input,
        require_approval=True,
        require_audit=False,  # audit available after write
        require_output_manifest=False,  # manifest generated during execution
    )

    if report.status == "fail":
        return {
            "ok": False,
            "status": "EXECUTION_CONSISTENCY_FAILED",
            "dry_run": False,
            "would_execute": False,
            "execution_allowed": False,
            "execution_consistency": report.model_dump(),
            "execution": _execution_meta(),
        }

    # Pass or warning — continue; attach report for diagnostics
    return None


def _ticket_roots(
    *,
    context: ProjectContext,
    settings: ProjectSettings,
    written_path: Path,
) -> tuple[list[str], list[str]]:
    input_roots: set[str] = {
        str(Path(settings.source_path).resolve().parent),
    }
    output_roots: set[str] = {
        str(Path(settings.runtime.work_dir).resolve()),
        str(Path(settings.runtime.log_dir).resolve()),
        str(Path(settings.runtime.derivatives_dir).resolve()),
        str(Path(settings.runtime.report_dir).resolve()),
        str(written_path.resolve().parent),
    }
    if context.project_dir is not None:
        input_roots.add(str(context.project_dir.resolve()))
        output_roots.add(str(context.project_dir.resolve()))
        output_roots.add(str((context.project_dir / "derivatives").resolve()))
        output_roots.add(str((context.project_dir / "work").resolve()))
    if context.rawdata_dir is not None:
        input_roots.add(str(context.rawdata_dir.resolve()))
        rawdata = context.rawdata_dir.resolve()
        output_roots = {root for root in output_roots if Path(root).resolve() != rawdata}
    if context.dataset_index_path is not None:
        input_roots.add(str(context.dataset_index_path.resolve().parent))
    return sorted(input_roots), sorted(output_roots)


def _approval_summary_hash(
    *,
    reviewed_plan: ReviewedPlanRecord,
    approval: dict[str, Any] | None,
) -> str:
    raw = reviewed_plan.payload.get("approval_envelope")
    if not isinstance(raw, dict):
        raise SafetyError("APPROVAL_SUMMARY_MISSING", code="APPROVAL_SUMMARY_MISSING")
    summary = ApprovalSummary.model_validate(raw)
    ApprovalSummaryService().verify(summary)
    provided = str((approval or {}).get("approval_summary_hash") or "")
    if (
        not provided
        or provided != summary.summary_hash
        or summary.reviewed_plan_id != reviewed_plan.reviewed_plan_id
        or summary.plan_hash != reviewed_plan.plan_hash
        or summary.memory_context_hash != reviewed_plan.memory_context_hash
    ):
        raise SafetyError("APPROVAL_SUMMARY_STALE", code="APPROVAL_SUMMARY_STALE")
    return summary.summary_hash


# ── Main endpoint ────────────────────────────────────────────────────────────


def _execute_reviewed_application(request: ExecuteReviewedRequest) -> dict[str, Any]:
    """Validate (and optionally execute) a reviewed plan.

    dry_run=true  → readiness check only.
    dry_run=false → safe execution preflight + gated execution
                    (safe allowlist only).
    """
    plan = request.plan
    context: ProjectContext | None = None

    # ═══════════════════════════════════════════════════════════════════════════
    # dry_run=false → execution preflight (M5-T015) + gated execution (M5-T016)
    # ═══════════════════════════════════════════════════════════════════════════
    if request.dry_run is not True:
        # 1. Env var gate
        if not _is_preflight_enabled():
            return _early_blocked("REVIEWED_EXECUTION_DISABLED", request)

        # 2. Confirm execution
        if not request.confirm_execution:
            return _early_blocked("CONFIRMATION_REQUIRED", request)

        # 3. Audit required
        if not request.persist_audit:
            return _early_blocked("AUDIT_REQUIRED", request)

        # 4. Project config validation
        settings, pc_error = _validate_project_config(request.project_config_path)
        if pc_error:
            return _early_blocked(pc_error, request)

        context, context_status, context_errors = _check_project_context(
            plan,
            request.project_config_path,
            request.project_id,
        )
        if context_status:
            return _project_context_blocked(
                context_status,
                request,
                context_errors,
                context,
            )

        reviewed_plan: ReviewedPlanRecord | None = None
        if not context or context.source != "created":
            return _with_link_fields(
                _project_context_blocked(
                    "REVIEWED_PLAN_REQUIRED",
                    request,
                    [
                        "REVIEWED_PLAN_REQUIRED: real execution requires a persisted "
                        "project and reviewed-plan identity"
                    ],
                    context,
                ),
                reviewed_plan_id=request.reviewed_plan_id,
            )
        try:
            reviewed_plan = resolve_reviewed_plan_for_execution(
                context,
                plan,
                request.reviewed_plan_id,
            )
        except ReviewedPlanStoreError as exc:
            return _with_link_fields(
                _project_context_blocked(
                    _reviewed_plan_error_status(exc),
                    request,
                    [str(exc)],
                    context,
                ),
                reviewed_plan_id=request.reviewed_plan_id,
            )

        if not request.lifecycle_id:
            bound_lifecycles = [
                lifecycle
                for lifecycle in mock_store.list_agent_lifecycles(reviewed_plan.project_id)
                if lifecycle.reviewed_plan_id == reviewed_plan.reviewed_plan_id
            ]
            if bound_lifecycles:
                result = _early_blocked("AGENT_LIFECYCLE_ID_REQUIRED", request)
                result["errors"] = [
                    "AGENT_LIFECYCLE_ID_REQUIRED: this reviewed plan belongs to an Agent Task; "
                    "approve or retry it from the Agent workspace."
                ]
                return _with_link_fields(
                    result,
                    reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                )

        # 5. Re-validate plan
        validation_result = validate_plan(plan)
        validation = validation_result.to_dict()
        if not validation.get("ok"):
            result = {
                "ok": False,
                "status": "VALIDATION_FAILED",
                "dry_run": False,
                "would_execute": False,
                "execution_allowed": False,
                "validation": validation,
                "approval_gate": None,
                "adapter": None,
                "pipeline_yaml": _pipeline_yaml_default(),
                "plan_summary": _plan_summary(plan, validation),
                "project_config_path": request.project_config_path,
                "execution": _execution_meta(),
            }
            result["audit"] = _write_audit(
                "execution_blocked",
                plan,
                validation,
                request.approval,
                None,
                result,
                request.actor,
                request,
            )
            return result

        # 6. Re-check approval gate
        gate = check_approval_gate(plan, validation, request.approval).to_dict()
        if not gate.get("execution_allowed"):
            result = {
                "ok": False,
                "status": "APPROVAL_GATE_BLOCKED",
                "dry_run": False,
                "would_execute": False,
                "execution_allowed": False,
                "validation": validation,
                "approval_gate": gate,
                "adapter": None,
                "pipeline_yaml": _pipeline_yaml_default(),
                "plan_summary": _plan_summary(plan, validation),
                "project_config_path": request.project_config_path,
                "execution": _execution_meta(),
            }
            result["audit"] = _write_audit(
                "execution_blocked",
                plan,
                validation,
                request.approval,
                gate,
                result,
                request.actor,
                request,
            )
            return result

        # 7. Plan adapter
        adapter = adapt_reviewed_plan(plan)
        if not adapter.ok:
            return _blocked_result("PLAN_ADAPTER_FAILED", plan, validation, gate, adapter, request)
        if _is_policy_blocked(adapter.policy):
            return _blocked_result(
                "EXECUTION_POLICY_BLOCKED", plan, validation, gate, adapter, request
            )

        # 8. Safe allowlist check (M5-T016)
        allowlist_error = _check_safe_allowlist(adapter.policy)
        if allowlist_error:
            result = {
                "ok": False,
                "status": "SAFE_EXECUTION_POLICY_BLOCKED",
                "dry_run": False,
                "would_execute": False,
                "execution_allowed": False,
                "validation": validation,
                "approval_gate": gate,
                "adapter": _adapter_summary(adapter),
                "pipeline_yaml": _pipeline_yaml_default(),
                "plan_summary": _plan_summary(plan, validation),
                "project_config_path": request.project_config_path,
                "execution": _execution_meta(),
            }
            result["audit"] = _write_audit(
                "execution_blocked",
                plan,
                validation,
                request.approval,
                gate,
                result,
                request.actor,
                request,
            )
            return result
        plan = validation_result.normalized_plan

        native_readiness = _check_native_preproc_readiness(plan, request, context)
        if native_readiness is not None and not native_readiness.get("ok"):
            return _native_preproc_readiness_blocked_result(
                plan=plan,
                validation=validation,
                gate=gate,
                adapter=adapter,
                request=request,
                readiness=native_readiness,
            )

        run_link_id: str | None = None
        linked_run_id: str | None = None
        if reviewed_plan is not None:
            run_link_id, linked_run_id = new_run_identity()
            pipeline = adapter.pipeline
            execution_config = pipeline.setdefault("execution", {}) if pipeline else None
            if not isinstance(execution_config, dict):
                return _with_link_fields(
                    _blocked_result(
                        "PLAN_ADAPTER_FAILED",
                        plan,
                        validation,
                        gate,
                        adapter,
                        request,
                    ),
                    reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                    run_link_id=run_link_id,
                    run_id=linked_run_id,
                )
            execution_config["run_id"] = linked_run_id

        # 9. Pipeline YAML required for execution
        if not request.write_pipeline_yaml:
            result = {
                "ok": False,
                "status": "PIPELINE_YAML_REQUIRED",
                "dry_run": False,
                "would_execute": False,
                "execution_allowed": False,
                "validation": validation,
                "approval_gate": gate,
                "adapter": _adapter_summary(adapter),
                "pipeline_yaml": _pipeline_yaml_summary(would_write=True, written=False),
                "plan_summary": _plan_summary(plan, validation),
                "project_config_path": request.project_config_path,
                "execution": _execution_meta(),
            }
            result["audit"] = _write_audit(
                "execution_blocked",
                plan,
                validation,
                request.approval,
                gate,
                result,
                request.actor,
                request,
            )
            return _with_link_fields(
                result,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
                run_link_id=run_link_id,
                run_id=linked_run_id,
            )

        # 10. Write pipeline YAML
        py_info, writer_status, written_path = _try_write_pipeline_yaml(
            adapter,
            request,
            reviewed_plan.plan_hash if reviewed_plan else None,
        )
        if writer_status is not None:
            result = {
                "ok": False,
                "status": writer_status,
                "dry_run": False,
                "would_execute": False,
                "execution_allowed": False,
                "validation": validation,
                "approval_gate": gate,
                "adapter": _adapter_summary(adapter),
                "pipeline_yaml": py_info,
                "plan_summary": _plan_summary(plan, validation),
                "project_config_path": request.project_config_path,
                "execution": _execution_meta(),
            }
            result["audit"] = _write_audit(
                "execution_blocked",
                plan,
                validation,
                request.approval,
                gate,
                result,
                request.actor,
                request,
            )
            return _with_link_fields(
                result,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
                run_link_id=run_link_id,
                run_id=linked_run_id,
            )

        if reviewed_plan is not None:
            assert run_link_id is not None
            assert linked_run_id is not None
            assert written_path is not None
            run_link = build_run_link(
                project_id=reviewed_plan.project_id,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                run_link_id=run_link_id,
                run_id=linked_run_id,
                project_config_path=reviewed_plan.project_config_path,
                pipeline_path=str(written_path),
                task_id=request.lifecycle_id,
            )
            try:
                mock_store.add_run_link(run_link)
            except Exception as exc:
                return _with_link_fields(
                    {
                        "ok": False,
                        "status": "RUN_LINK_WRITE_FAILED",
                        "dry_run": False,
                        "would_execute": False,
                        "execution_allowed": False,
                        "validation": validation,
                        "approval_gate": gate,
                        "adapter": _adapter_summary(adapter),
                        "pipeline_yaml": py_info,
                        "plan_summary": _plan_summary(plan, validation),
                        "project_config_path": request.project_config_path,
                        "execution": _execution_meta(),
                        "audit": _no_audit(),
                        "errors": [f"RUN_LINK_WRITE_FAILED: {exc}"],
                    },
                    reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                    run_link_id=run_link_id,
                    run_id=linked_run_id,
                    pipeline_path=str(written_path),
                )

        # 11. Audit record → write BEFORE executor
        preflight_result = {
            "ok": True,
            "status": "EXECUTION_PREFLIGHT_READY",
            "dry_run": False,
            "would_execute": True,
            "execution_allowed": True,
            "validation": validation,
            "approval_gate": gate,
            "adapter": _adapter_summary(adapter),
            "pipeline_yaml": py_info,
            "plan_summary": _plan_summary(plan, validation),
            "project_config_path": request.project_config_path,
            "execution": _execution_meta(),
        }
        audit_info = _write_audit(
            "execution_requested",
            plan,
            validation,
            request.approval,
            gate,
            preflight_result,
            request.actor,
            request,
        )
        if not audit_info.get("persisted"):
            result = dict(preflight_result)
            result["ok"] = False
            result["status"] = "AUDIT_WRITE_FAILED"
            result["would_execute"] = False
            result["execution_allowed"] = False
            result["audit"] = audit_info
            if run_link_id:
                try:
                    mock_store.update_run_link(
                        run_link_id,
                        status="BLOCKED",
                        payload={"audit": audit_info},
                    )
                except Exception as exc:
                    result["warnings"] = [f"RUN_LINK_UPDATE_FAILED: {exc}"]
            return _with_link_fields(
                result,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
                run_link_id=run_link_id,
                run_id=linked_run_id,
                pipeline_path=str(written_path) if written_path else None,
            )

        if run_link_id and reviewed_plan:
            try:
                mock_store.update_run_link(
                    run_link_id,
                    status="RUNNING",
                    audit_id=str(audit_info.get("audit_id") or "") or None,
                    payload={"audit": audit_info},
                )
                mock_store.update_reviewed_plan(
                    reviewed_plan.reviewed_plan_id,
                    approval_status="APPROVED",
                    execution_status="RUNNING",
                    last_audit_id=str(audit_info.get("audit_id") or "") or None,
                    last_execution_id=run_link_id,
                )
            except Exception as exc:
                try:
                    mock_store.update_run_link(
                        run_link_id,
                        status="BLOCKED",
                        warnings=[f"RUN_LINK_UPDATE_FAILED: {exc}"],
                    )
                except Exception:
                    pass
                result = dict(preflight_result)
                result.update(
                    {
                        "ok": False,
                        "status": "RUN_LINK_UPDATE_FAILED",
                        "would_execute": False,
                        "execution_allowed": False,
                        "audit": audit_info,
                        "errors": [f"RUN_LINK_UPDATE_FAILED: {exc}"],
                    }
                )
                return _with_link_fields(
                    result,
                    reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                    run_link_id=run_link_id,
                    run_id=linked_run_id,
                    pipeline_path=str(written_path) if written_path else None,
                )

        # 12. Phase 3: dry-run/execute consistency preflight
        consistency_report = _run_consistency_preflight(
            plan=plan,
            reviewed_plan=reviewed_plan,
            request=request,
            adapter=adapter,
            audit_info=audit_info,
            project_id=(reviewed_plan.project_id if reviewed_plan else request.project_id or ""),
        )
        if consistency_report is not None:
            # consistency failure → block execution
            return _with_link_fields(
                consistency_report,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
                run_link_id=run_link_id,
                run_id=linked_run_id,
                pipeline_path=str(written_path) if written_path else None,
            )

        # 13. Issue a server-side capability and dispatch through the sole gateway.
        try:
            assert reviewed_plan is not None
            assert context is not None
            assert settings is not None
            assert written_path is not None
            approval_summary_hash = _approval_summary_hash(
                reviewed_plan=reviewed_plan,
                approval=request.approval,
            )
            approval_summary = ApprovalSummary.model_validate(
                reviewed_plan.payload["approval_envelope"]
            )
            input_roots, output_roots = _ticket_roots(
                context=context,
                settings=settings,
                written_path=written_path,
            )
            nodes = [node for node in plan.get("nodes", []) if isinstance(node, dict)]
            goal_contract = reviewed_plan.payload.get("goal_contract")
            if not isinstance(goal_contract, dict):
                raise SafetyError(
                    "REVIEWED_PLAN_NEEDS_GOAL_REVIEW",
                    code="REVIEWED_PLAN_NEEDS_GOAL_REVIEW",
                )
            goal_contract_hash = str(goal_contract.get("goal_contract_hash") or "")
            evaluation_policy_version = str(goal_contract.get("evaluation_policy_version") or "")
            if not goal_contract_hash or not evaluation_policy_version:
                raise SafetyError(
                    "GOAL_CONTRACT_BINDING_REQUIRED",
                    code="GOAL_CONTRACT_BINDING_REQUIRED",
                )
            ticket_service = ExecutionTicketService(mock_store)
            issued_ticket = ticket_service.issue(
                project_id=reviewed_plan.project_id,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                plan_hash=reviewed_plan.plan_hash,
                approval_summary_hash=approval_summary_hash,
                execution_environment_snapshot_id=approval_summary.execution_environment_snapshot_id,
                execution_environment_hash=approval_summary.execution_environment_hash,
                sandbox_policies=approval_summary.sandbox_policies,
                sandbox_policy_version=approval_summary.sandbox_policy_version,
                sandbox_policies_hash=approval_summary.sandbox_policies_hash,
                memory_context_hash=reviewed_plan.memory_context_hash,
                approved_actor=str(
                    (request.approval or {}).get("approved_by") or request.actor or "local-user"
                ),
                approved_node_ids=[str(node.get("id") or "") for node in nodes],
                approved_backend_ids=[str(node.get("backend") or "unknown") for node in nodes],
                input_roots=input_roots,
                output_roots=output_roots,
                readonly_roots=(
                    [str(context.rawdata_dir.resolve())] if context.rawdata_dir is not None else []
                ),
                project_config_path=str(request.project_config_path),
                pipeline_path=str(written_path),
                allowlist_hash=current_allowlist_hash(),
                normalized_params_hash=validation_result.normalized_params_hash,
                contract_versions=validation_result.contract_versions,
                audit_id=str(audit_info.get("audit_id") or ""),
                goal_contract_hash=goal_contract_hash,
                evaluation_policy_version=evaluation_policy_version,
                max_retry_count=0,
            )
            orchestrator = AgentOrchestrator(mock_store)
            if request.lifecycle_id:
                lifecycle = orchestrator.get(
                    project_id=reviewed_plan.project_id,
                    lifecycle_id=request.lifecycle_id,
                )
                if lifecycle.state != "WAITING_FOR_APPROVAL":
                    raise SafetyError(
                        "LIFECYCLE_APPROVAL_STATE_REQUIRED",
                        code="LIFECYCLE_APPROVAL_STATE_REQUIRED",
                    )
                if lifecycle.reviewed_plan_id != reviewed_plan.reviewed_plan_id:
                    raise SafetyError(
                        "LIFECYCLE_BINDING_DRIFT",
                        code="LIFECYCLE_BINDING_DRIFT",
                    )
                lifecycle = orchestrator.transition(
                    project_id=lifecycle.project_id,
                    lifecycle_id=lifecycle.lifecycle_id,
                    to_state="APPROVED",
                    command_id=f"approve:{issued_ticket.execution_ticket_id}",
                    actor=issued_ticket.approved_actor,
                    source_command="agent_approval",
                )
                lifecycle = orchestrator.transition(
                    project_id=lifecycle.project_id,
                    lifecycle_id=lifecycle.lifecycle_id,
                    to_state="EXECUTION_READY",
                    command_id=f"ready:{issued_ticket.execution_ticket_id}",
                    actor=issued_ticket.approved_actor,
                    source_command="execution_ticket_issued",
                    updates={
                        "execution_ticket_id": issued_ticket.execution_ticket_id,
                        "audit_id": issued_ticket.audit_id,
                    },
                )
            else:
                lifecycle = orchestrator.prepare_reviewed_execution(
                    project_id=reviewed_plan.project_id,
                    reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                    execution_ticket_id=issued_ticket.execution_ticket_id,
                    audit_id=issued_ticket.audit_id,
                    actor=issued_ticket.approved_actor,
                )
            assert run_link_id is not None
            associated_link = mock_store.update_run_link(
                run_link_id,
                task_id=lifecycle.lifecycle_id,
            )
            if associated_link is None or associated_link.task_id != lifecycle.lifecycle_id:
                raise SafetyError(
                    "RUN_LINK_TASK_ASSOCIATION_FAILED",
                    code="RUN_LINK_TASK_ASSOCIATION_FAILED",
                )
            executor_result, consumed_ticket, lifecycle = orchestrator.dispatch_execution(
                lifecycle=lifecycle,
                actor=issued_ticket.approved_actor,
                dispatch=lambda: ExecutionGateway(ticket_service).dispatch(
                    execution_ticket_id=issued_ticket.execution_ticket_id,
                    project_id=reviewed_plan.project_id,
                    reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                    plan_hash=reviewed_plan.plan_hash,
                    approval_summary_hash=approval_summary_hash,
                    memory_context_hash=reviewed_plan.memory_context_hash,
                    scope_hash=issued_ticket.scope_hash,
                    normalized_params_hash=validation_result.normalized_params_hash,
                    contract_versions=validation_result.contract_versions,
                    project_config_path=str(request.project_config_path),
                    pipeline_path=str(written_path),
                    command_id=(
                        request.command_id
                        or f"execute:{issued_ticket.execution_ticket_id}"
                    ),
                    run_id=linked_run_id,
                    goal_contract_hash=goal_contract_hash,
                    evaluation_policy_version=evaluation_policy_version,
                ),
            )
        except Exception as exc:
            failure_code = (
                str(exc.code)
                if isinstance(exc, SafetyError) and exc.code
                else "EXECUTION_FAILED"
            )
            recovery = {
                "recoverable": failure_code
                in {
                    "EXECUTION_TICKET_EXPIRED",
                    "GATEWAY_DISPATCH_OUTCOME_UNKNOWN",
                    "EXECUTION_DISPATCH_FAILED",
                },
                "next_step": (
                    "REVIEW_AND_APPROVE_NEW_PLAN"
                    if failure_code == "EXECUTION_TICKET_EXPIRED"
                    else "INSPECT_PERSISTED_DISPATCH"
                    if failure_code == "GATEWAY_DISPATCH_OUTCOME_UNKNOWN"
                    else "REVIEW_EXECUTION_FAILURE"
                ),
            }
            if run_link_id and reviewed_plan:
                try:
                    persisted_dispatch = (
                        mock_store.get_gateway_dispatch_by_ticket(
                            issued_ticket.execution_ticket_id
                        )
                        if "issued_ticket" in locals()
                        else None
                    )
                    mock_store.update_run_link(
                        run_link_id,
                        status="FAILED",
                        dispatch_id=(
                            persisted_dispatch.dispatch_id
                            if persisted_dispatch is not None
                            else None
                        ),
                        payload={"audit": audit_info, "error": str(exc)},
                    )
                    mock_store.update_reviewed_plan(
                        reviewed_plan.reviewed_plan_id,
                        execution_status="FAILED",
                        last_execution_id=run_link_id,
                    )
                except Exception:
                    pass
            result = {
                "ok": False,
                "status": failure_code,
                "dry_run": False,
                "would_execute": False,
                "execution_allowed": False,
                "validation": validation,
                "approval_gate": gate,
                "adapter": _adapter_summary(adapter),
                "pipeline_yaml": py_info,
                "plan_summary": _plan_summary(plan, validation),
                "project_config_path": request.project_config_path,
                "execution": _execution_meta(executor_called=True),
                "execution_ticket": (
                    issued_ticket.model_dump(mode="json") if "issued_ticket" in locals() else None
                ),
                "lifecycle": (
                    lifecycle.model_dump(mode="json") if "lifecycle" in locals() else None
                ),
                "audit": audit_info,
                "errors": [failure_code],
                "recovery": recovery,
            }
            return _with_link_fields(
                result,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
                run_link_id=run_link_id,
                run_id=linked_run_id,
                pipeline_path=str(written_path) if written_path else None,
            )

        # 13. Executor returned — success
        executor_run_id = (
            executor_result.get("run_id") if isinstance(executor_result, dict) else None
        )
        summary_path = (
            executor_result.get("summary_path") if isinstance(executor_result, dict) else None
        )
        dispatch_id = str(executor_result.get("dispatch_id") or "") or None
        response_warnings: list[str] = []
        if linked_run_id and executor_run_id and executor_run_id != linked_run_id:
            response_warnings.append(
                "EXECUTOR_RUN_ID_MISMATCH: executor returned a different run_id"
            )
        if run_link_id and reviewed_plan:
            execution_status = (
                str(executor_result.get("status") or "SUBMITTED")
                if isinstance(executor_result, dict)
                else "SUBMITTED"
            )
            try:
                mock_store.update_run_link(
                    run_link_id,
                    status=execution_status,
                    dispatch_id=dispatch_id,
                    summary_path=str(summary_path) if summary_path else None,
                    payload={"audit": audit_info, "executor_result": executor_result},
                    warnings=response_warnings,
                )
                mock_store.update_reviewed_plan(
                    reviewed_plan.reviewed_plan_id,
                    execution_status=execution_status,
                    last_execution_id=run_link_id,
                )
            except Exception as exc:
                response_warnings.append(f"RUN_LINK_FINALIZE_FAILED: {exc}")
        executor_failed = False
        executor_errors: list[str] = []
        if isinstance(executor_result, dict):
            raw_status = str(executor_result.get("status") or "").upper()
            executor_failed = (
                raw_status in {"FAILED", "FAILURE", "ERROR"} or executor_result.get("ok") is False
            )
            raw_errors = executor_result.get("errors")
            if isinstance(raw_errors, list):
                executor_errors = [str(item) for item in raw_errors]
            elif raw_errors:
                executor_errors = [str(raw_errors)]
            if executor_failed and not executor_errors:
                executor_errors = [f"Executor returned failure status: {raw_status or 'UNKNOWN'}"]
        if executor_failed:
            if (
                "orchestrator" in locals()
                and "lifecycle" in locals()
                and lifecycle.state == "RUNNING"
            ):
                lifecycle = orchestrator.transition(
                    project_id=lifecycle.project_id,
                    lifecycle_id=lifecycle.lifecycle_id,
                    to_state="FAILED",
                    command_id=f"executor:{consumed_ticket.execution_ticket_id}:failed",
                    actor=consumed_ticket.approved_actor,
                    source_command="executor_failed",
                    reason="; ".join(executor_errors),
                    updates={"last_error": "; ".join(executor_errors)},
                )
            result = {
                "ok": False,
                "status": "EXECUTION_FAILED",
                "dry_run": False,
                "would_execute": True,
                "execution_allowed": True,
                "validation": validation,
                "approval_gate": gate,
                "adapter": _adapter_summary(adapter),
                "pipeline_yaml": py_info,
                "plan_summary": _plan_summary(plan, validation),
                "project_config_path": request.project_config_path,
                "execution": _execution_meta(
                    submitted=True,
                    run_id=linked_run_id or executor_run_id,
                    executor_called=True,
                ),
                "executor_result": executor_result,
                "lifecycle": lifecycle.model_dump(mode="json"),
                "audit": audit_info,
                "errors": executor_errors,
                "warnings": response_warnings,
            }
            return _with_link_fields(
                result,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
                run_link_id=run_link_id,
                run_id=linked_run_id or executor_run_id,
                pipeline_path=str(written_path) if written_path else None,
                summary_path=str(summary_path) if summary_path else None,
            )
        if lifecycle.state == "RUNNING":
            try:
                reconciler = AgentTaskReconciler(mock_store)
                lifecycle = reconciler.reconcile_once(
                    project_id=lifecycle.project_id,
                    lifecycle_id=lifecycle.lifecycle_id,
                )
                if lifecycle.state == "RUNNING":
                    reconciler.start_bounded_monitor(
                        project_id=lifecycle.project_id,
                        lifecycle_id=lifecycle.lifecycle_id,
                    )
            except Exception as exc:
                response_warnings.append(
                    f"LIFECYCLE_TERMINAL_COORDINATION_FAILED: {exc}"
                )
        result = {
            "ok": True,
            "status": "EXECUTION_SUBMITTED",
            "dry_run": False,
            "would_execute": True,
            "execution_allowed": True,
            "validation": validation,
            "approval_gate": gate,
            "adapter": _adapter_summary(adapter),
            "pipeline_yaml": py_info,
            "plan_summary": _plan_summary(plan, validation),
            "project_config_path": request.project_config_path,
            "execution": _execution_meta(
                submitted=True,
                run_id=linked_run_id or executor_run_id,
                executor_called=True,
            ),
            "executor_result": executor_result,
            "execution_ticket": consumed_ticket.model_dump(mode="json"),
            "lifecycle": lifecycle.model_dump(mode="json"),
            "audit": audit_info,
            "warnings": response_warnings,
        }
        return _with_link_fields(
            result,
            reviewed_plan_id=reviewed_plan.reviewed_plan_id if reviewed_plan else None,
            run_link_id=run_link_id,
            run_id=linked_run_id or executor_run_id,
            pipeline_path=str(written_path) if written_path else None,
            summary_path=str(summary_path) if summary_path else None,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # dry_run=true → readiness check (M5-T005..T014 — unchanged behaviour)
    # ═══════════════════════════════════════════════════════════════════════════

    # ── 1. Re-validate plan ──
    if request.project_config_path:
        settings, pc_error = _validate_project_config(request.project_config_path)
        if pc_error:
            return _early_blocked(pc_error, request)
        context, context_status, context_errors = _check_project_context(
            plan,
            request.project_config_path,
            request.project_id,
        )
        if context_status:
            return _project_context_blocked(
                context_status,
                request,
                context_errors,
                context,
            )

    validation_result = validate_plan(plan)
    validation = validation_result.to_dict()

    if not validation.get("ok"):
        result = {
            "ok": False,
            "status": "VALIDATION_FAILED",
            "dry_run": True,
            "would_execute": False,
            "execution_allowed": False,
            "validation": validation,
            "approval_gate": None,
            "adapter": None,
            "pipeline_yaml": _pipeline_yaml_default(),
            "plan_summary": _plan_summary(plan, validation),
            "project_config_path": request.project_config_path,
            "execution": _execution_meta(),
        }
        result["audit"] = _write_audit(
            "execution_blocked",
            plan,
            validation,
            request.approval,
            None,
            result,
            request.actor,
            request,
        )
        return result
    plan = validation_result.normalized_plan

    # ── 2. Re-check approval gate ──
    gate = check_approval_gate(plan, validation, request.approval).to_dict()

    if not gate.get("execution_allowed"):
        result = {
            "ok": False,
            "status": "APPROVAL_GATE_BLOCKED",
            "dry_run": True,
            "would_execute": False,
            "execution_allowed": False,
            "validation": validation,
            "approval_gate": gate,
            "adapter": None,
            "pipeline_yaml": _pipeline_yaml_default(),
            "plan_summary": _plan_summary(plan, validation),
            "project_config_path": request.project_config_path,
            "execution": _execution_meta(),
        }
        result["audit"] = _write_audit(
            "execution_blocked",
            plan,
            validation,
            request.approval,
            gate,
            result,
            request.actor,
            request,
        )
        return result

    # ── 3. Plan adapter check ──
    adapter = adapt_reviewed_plan(plan)

    if not adapter.ok:
        return _blocked_result("PLAN_ADAPTER_FAILED", plan, validation, gate, adapter, request)

    if _is_policy_blocked(adapter.policy):
        return _blocked_result("EXECUTION_POLICY_BLOCKED", plan, validation, gate, adapter, request)

    native_readiness = _check_native_preproc_readiness(plan, request, context)
    if native_readiness is not None and not native_readiness.get("ok"):
        return _native_preproc_readiness_blocked_result(
            plan=plan,
            validation=validation,
            gate=gate,
            adapter=adapter,
            request=request,
            readiness=native_readiness,
        )

    # ── 4. Pipeline writer check ──
    py_info, writer_status, written_path = _try_write_pipeline_yaml(adapter, request)
    if writer_status is not None:
        result = {
            "ok": False,
            "status": writer_status,
            "dry_run": True,
            "would_execute": False,
            "execution_allowed": False,
            "validation": validation,
            "approval_gate": gate,
            "adapter": _adapter_summary(adapter),
            "pipeline_yaml": py_info,
            "plan_summary": _plan_summary(plan, validation),
            "project_config_path": request.project_config_path,
            "execution": _execution_meta(),
        }
        result["audit"] = _write_audit(
            "execution_blocked",
            plan,
            validation,
            request.approval,
            gate,
            result,
            request.actor,
            request,
        )
        return result

    # ── 5. Dry-run OK ──
    result = {
        "ok": True,
        "status": "DRY_RUN_OK",
        "dry_run": True,
        "would_execute": True,
        "execution_allowed": True,
        "validation": validation,
        "approval_gate": gate,
        "adapter": _adapter_summary(adapter),
        "pipeline_yaml": py_info,
        "plan_summary": _plan_summary(plan, validation),
        "project_config_path": request.project_config_path,
        "execution": _execution_meta(),
    }
    result["audit"] = _write_audit(
        "dry_run_checked",
        plan,
        validation,
        request.approval,
        gate,
        result,
        request.actor,
        request,
    )
    return result
