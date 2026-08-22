"""DICOM conversion domain router — extracted from dashboard_routes.py.

All conversion endpoints are preserved with identical URL paths and response
contracts.  The new router uses ``Depends(get_project_store)`` so route
handlers do not reach for the global ``mock_store`` directly.

The canonical conversion routes live here. ``dashboard_routes.py`` only
retains helper functions still used by characterization tests.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.api.execution_contract import reject_execution_contract
from src.backend.app.schemas.desktop import (
    ConversionDryRunRequest,
    ConversionDryRunResponse,
)
from src.backend.app.schemas.dicom_conversion_prepare import (
    DicomConversionPrepareRequest,
)
from src.backend.app.services.dicom_conversion_service import (
    run_conversion_dry_run,
    run_conversion_persist_plan,
    run_conversion_preflight,
    run_export_conversion_review_package,
    run_get_conversion_release_readiness,
    run_get_conversion_review_package,
    run_get_latest_conversion_dry_run,
)

router = APIRouter()


# ── Dry-run ───────────────────────────────────────────────────────────────

@router.post(
    "/api/projects/{project_id}/conversion/dry-run",
    response_model=ConversionDryRunResponse,
    summary="Build a deterministic DICOM conversion dry-run plan",
)
def post_conversion_dry_run(
    project_id: str,
    request: ConversionDryRunRequest = ConversionDryRunRequest(),
    store: ProjectStore = Depends(get_project_store),
) -> ConversionDryRunResponse:
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return run_conversion_dry_run(store, project_id, request)


@router.get(
    "/api/projects/{project_id}/conversion/dry-run/latest",
    response_model=ConversionDryRunResponse,
    summary="Restore latest persisted DICOM conversion dry-run mapping snapshot",
)
def get_latest_conversion_dry_run(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> ConversionDryRunResponse:
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return run_get_latest_conversion_dry_run(store, project_id)


# ── Preflight ─────────────────────────────────────────────────────────────

@router.post(
    "/api/projects/{project_id}/conversion/preflight",
    summary="Check DICOM conversion prerequisites",
)
def post_conversion_preflight(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return run_conversion_preflight(store, project_id)


# ── Prepare (unified approval + execution preparation) ────────────────────
# Per 实现dcm2nii任务方案.md §13, this is the single-call orchestration
# endpoint that performs all preparation steps and returns the authoritative
# readiness state.  Not marked deprecated — this is the canonical endpoint.

@router.post(
    "/api/projects/{project_id}/dicom-conversion/prepare",
    summary="Prepare DICOM conversion: validate, persist approval, reserve run",
)
def post_dicom_conversion_prepare(
    project_id: str,
    request: DicomConversionPrepareRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    """Prepare DICOM conversion per 实现dcm2nii任务方案.md §13.

    Performs all system validations, persists the approval package,
    reserves a conversion run directory, and returns the authoritative
    readiness state in a single call.
    """
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.services.dicom_conversion_prepare import (
        run_dicom_conversion_prepare,
    )
    response = run_dicom_conversion_prepare(store, project_id, request)
    return response.model_dump()


# ── Approval / plan persistence ──────────────────────────────────────────

@router.post(
    "/api/projects/{project_id}/conversion/approval/persist-plan",
    summary="Persist a reviewed DICOM conversion plan",
)
def post_conversion_persist_plan(
    project_id: str,
    body: dict[str, Any],
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return run_conversion_persist_plan(store, project_id, body)


# ── Result registration (实现dcm2nii任务方案.md §17) ──────────────────────
# Registers successful conversion outputs into project metadata so that
# Dashboard, Viewer, and project state can refresh automatically.

@router.post(
    "/api/projects/{project_id}/dicom-conversion/register-result",
    summary="Register conversion result into project metadata",
)
def post_dicom_conversion_register_result(
    project_id: str,
    body: dict[str, Any],
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    """Register conversion result per 实现dcm2nii任务方案.md §17.

    Updates project metadata with conversion summary and triggers
    Dashboard/Viewer refresh signals.
    """
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.services.dicom_conversion_result_registration import (
        register_conversion_result,
    )
    return register_conversion_result(
        store,
        project_id,
        conversion_run_id=str(body.get("conversion_run_id") or ""),
        output_root=str(body.get("output_root") or ""),
        execution_status=str(body.get("execution_status") or "succeeded"),
        mapping_count=int(body.get("mapping_count") or 0),
        nifti_count=int(body.get("nifti_count") or 0),
        bold_count=int(body.get("bold_count") or 0),
        t1w_count=int(body.get("t1w_count") or 0),
        subject_count=int(body.get("subject_count") or 0),
        manifest_path=body.get("manifest_path"),
        provenance_path=body.get("provenance_path"),
        checksum_verified=bool(body.get("checksum_verified") or False),
    )


# ── Review package ────────────────────────────────────────────────────────

@router.get(
    "/api/projects/{project_id}/conversion/approval/packages/{conversion_run_id}",
    summary="Read a DICOM conversion review package",
)
def get_conversion_review_package(
    project_id: str,
    conversion_run_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return run_get_conversion_review_package(store, project_id, conversion_run_id)


@router.post(
    "/api/projects/{project_id}/conversion/approval/packages/{conversion_run_id}/export",
    summary="Export a DICOM conversion review package",
)
def post_conversion_review_package_export(
    project_id: str,
    conversion_run_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return run_export_conversion_review_package(store, project_id, conversion_run_id)


# ── Release readiness ─────────────────────────────────────────────────────

@router.get(
    "/api/projects/{project_id}/conversion/release-readiness/{conversion_run_id}",
    summary="Read DICOM conversion release readiness",
)
def get_conversion_release_readiness(
    project_id: str,
    conversion_run_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    if not store.get_project(project_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return run_get_conversion_release_readiness(store, project_id, conversion_run_id)


# ── Public execute ────────────────────────────────────────────────────────

@router.post(
    "/api/projects/{project_id}/conversion/execute",
    summary="Execute an approved DICOM conversion",
)
def post_conversion_execute(
    project_id: str,
    request_raw: dict[str, Any] | None = None,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    reject_execution_contract("conversion.execute", project_id=project_id)
