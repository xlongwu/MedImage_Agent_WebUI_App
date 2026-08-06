"""Native preprocessing node registry plugin."""

from __future__ import annotations

from typing import Any

from src.backend.app.native_preproc.orchestrator.stage_graph import iter_native_full_stage_specs
from src.backend.app.runtime.node_registry_plugins.base import NodeExecutionContext, NodeRunner
from src.backend.app.schemas.native_preproc_api import AcpcRequest, NativeFullPreprocRequest
from src.backend.app.schemas.pipeline_schema import PipelineNode
from src.backend.app.services.mock_store import mock_store
from src.backend.app.services.native_preproc_full import (
    run_native_full_dry_run,
    run_native_full_execute,
)
from src.backend.app.services.native_preproc_request import build_native_full_request
from src.backend.app.services.native_acpc import execute_acpc_request
from src.backend.app.services.reviewed_native_conversion_handoff import (
    ensure_reviewed_native_conversion_handoff,
)


def _project_store(context: NodeExecutionContext) -> Any:
    tool_context = context.tool_execution_context
    if tool_context is not None:
        return tool_context.ticket_service.store
    return mock_store


def _request_from_node(
    context: NodeExecutionContext, node: PipelineNode
) -> NativeFullPreprocRequest:
    return build_native_full_request(node.params, fallback_run_id=context.run_id)


def run_native_full_dry_run_node(
    context: NodeExecutionContext,
    node: PipelineNode,
) -> dict[str, Any]:
    request = _request_from_node(context, node)
    store = _project_store(context)
    project_id = str(
        node.params.get("project_id") or context.project_config.get("project_id") or ""
    )
    project = store.get_project(project_id) if project_id else None
    project_metadata = project.metadata if project is not None else {}
    project_dir = str(
        node.params.get("project_dir")
        or project_metadata.get("project_dir")
        or context.project_config.get("project_dir")
        or ""
    )
    result = run_native_full_dry_run(
        project_id,
        request,
        project_dir=project_dir,
        project_metadata=project_metadata,
    )
    payload = result.model_dump(mode="json")
    payload["node_id"] = node.id
    return payload


def run_native_full_execute_node(
    context: NodeExecutionContext,
    node: PipelineNode,
) -> dict[str, Any]:
    request = _request_from_node(context, node)
    scheduler = context.project_config.get("scheduler")
    scheduler_gpu_mode = (
        str(scheduler.get("gpu_mode") or "prefer") if isinstance(scheduler, dict) else "prefer"
    )
    requires_gpu = request.compute_policy.backend == "gpu" or any(
        backend == "gpu" for backend in request.compute_policy.stage_backends.values()
    )
    if requires_gpu and scheduler_gpu_mode == "off":
        return {
            "ok": False,
            "status": "blocked",
            "backend": "native_python",
            "node_id": node.id,
            "errors": [
                "GPU_POLICY_CONFLICT: reviewed compute policy requires GPU while project scheduler gpu_mode is off."
            ],
            "safety_flags": {
                "rawdata_not_modified": True,
                "no_external_tools_executed": True,
                "no_matlab_spm_dpabi": True,
            },
        }
    store = _project_store(context)
    project_id = str(
        node.params.get("project_id") or context.project_config.get("project_id") or ""
    )
    project = store.get_project(project_id) if project_id else None
    project_metadata = project.metadata if project is not None else {}
    project_dir = str(
        node.params.get("project_dir")
        or project_metadata.get("project_dir")
        or context.project_config.get("project_dir")
        or ""
    )
    conversion_handoff: dict[str, Any] | None = None
    if request.conversion_run_id:
        if context.tool_execution_context is None:
            return {
                "ok": False,
                "status": "blocked",
                "backend": "native_python",
                "node_id": node.id,
                "errors": [
                    "VERIFIED_EXECUTION_CONTEXT_REQUIRED: conversion handoff must execute through the reviewed gateway."
                ],
                "safety_flags": {
                    "rawdata_not_modified": True,
                    "no_external_tools_executed": True,
                    "no_matlab_spm_dpabi": True,
                },
            }
        rawdata_dir = str(
            node.params.get("rawdata_dir")
            or project_metadata.get("rawdata_dir")
            or context.project_config.get("rawdata_dir")
            or ""
        )
        conversion_handoff = ensure_reviewed_native_conversion_handoff(
            store,
            project_id=project_id,
            conversion_run_id=request.conversion_run_id,
            project_dir=project_dir,
            rawdata_dir=rawdata_dir,
            execution_context=context.tool_execution_context,
        )
        if not conversion_handoff.get("ok") and conversion_handoff.get("status") != "partial":
            return {
                "ok": False,
                "status": str(conversion_handoff.get("status") or "blocked"),
                "backend": "native_python",
                "node_id": node.id,
                "conversion_handoff": conversion_handoff,
                "errors": list(conversion_handoff.get("errors") or []),
                "blocking_issues": list(conversion_handoff.get("blocking_issues") or []),
                "safety_flags": {
                    "rawdata_not_modified": True,
                    "no_external_tools_executed": True,
                    "no_matlab_spm_dpabi": True,
                },
            }
        project = store.get_project(project_id) if project_id else None
        project_metadata = project.metadata if project is not None else project_metadata
    result = run_native_full_execute(
        project_id,
        request,
        project_dir=project_dir,
        project_metadata=project_metadata,
    )
    payload = result.model_dump(mode="json")
    payload["node_id"] = node.id
    if conversion_handoff is not None:
        payload["conversion_handoff"] = conversion_handoff
        if not conversion_handoff.get("ok"):
            payload["ok"] = False
            payload["status"] = "partial"
    return payload


def run_native_auto_acpc_align_node(
    context: NodeExecutionContext,
    node: PipelineNode,
) -> dict[str, Any]:
    """Execute the standalone, reviewed T1w-only ACPC node."""

    if context.tool_execution_context is None:
        return {
            "ok": False,
            "status": "blocked",
            "backend": "native_python",
            "node_id": node.id,
            "errors": ["REVIEWED_EXECUTION_CONTEXT_REQUIRED"],
            "warnings": [],
        }

    payload = dict(node.params)
    payload.setdefault("project_id", str(context.project_config.get("project_id") or ""))
    payload.setdefault("project_dir", str(context.project_config.get("project_dir") or ""))
    payload.setdefault("output_root", str(context.derivatives_dir or ""))
    result = execute_acpc_request(AcpcRequest.model_validate(payload), run_id=context.run_id)
    response = result.model_dump(mode="json")
    response["node_id"] = node.id
    response["backend"] = "native_python"
    response["safety_flags"] = {
        "rawdata_not_modified": True,
        "no_external_tools_executed": True,
        "no_matlab_spm_dpabi": True,
    }
    return response


def _run_native_stage_boundary_node(
    context: NodeExecutionContext,
    node: PipelineNode,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "blocked",
        "backend": "native_python",
        "node_id": node.id,
        "warnings": [],
        "errors": [
            "Native stage node is registered as a stable reviewed-plan boundary; "
            "execute via native_preproc_full_execute so manifest, provenance, "
            "artifact validation, and status truthfulness remain coordinated."
        ],
        "safety_flags": {
            "no_external_tools_executed": True,
            "no_matlab_spm_dpabi": True,
            "third_party_runtime_not_used": True,
        },
    }


REGISTRY: dict[str, NodeRunner] = {
    "native_preproc_full_dry_run": run_native_full_dry_run_node,
    "native_preproc_full_execute": run_native_full_execute_node,
    "native_auto_acpc_align": run_native_auto_acpc_align_node,
}

for _spec in iter_native_full_stage_specs():
    REGISTRY.setdefault(_spec.node_id, _run_native_stage_boundary_node)


__all__ = ["REGISTRY"]
