"""QC domain routes — extracted from dashboard_routes.py.

All endpoints mirror the original behavior; the only change is store access
via ``Depends(get_project_store)`` instead of the module-level ``mock_store``.
The canonical routes live here; ``dashboard_routes.py`` only retains helper
functions still used by characterization tests.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.services.artifact_adapter import (
    build_nifti_thumbnail,
)
from src.backend.app.services.qc_adapter import (
    build_bold_reference_readiness,
    build_data_readiness,
    build_motion_metrics_draft,
    build_motion_qc_readiness,
    build_nifti_qc_snapshot,
    build_qc_dashboard_fingerprint,
    build_qc_dashboard_report,
    build_rsfmri_qc_planning_report,
    build_spm_realign_dry_run,
    build_spm_realign_wrapper_skeleton,
    load_latest_qc_dashboard_report,
    validate_bids,
)

router = APIRouter()

# QC Dashboard


@router.post(
    "/api/projects/{project_id}/qc-dashboard/report",
    response_model=dict[str, object],
)
def post_qc_dashboard_report(
    project_id: str,
    cache: str = Query(default="off"),
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_qc_dashboard_report(
        project_id=project_id,
        cache=cache,
        store=store,
    )


@router.get(
    "/api/projects/{project_id}/qc-dashboard/report/latest",
    response_model=dict[str, object],
)
def get_latest_qc_dashboard_report(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    return load_latest_qc_dashboard_report(project_id=project_id, store=store)


@router.get(
    "/api/projects/{project_id}/qc-dashboard/fingerprint",
    response_model=dict[str, object],
)
def get_qc_dashboard_fingerprint(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_qc_dashboard_fingerprint(project_id=project_id, store=store)


# Data readiness


@router.get(
    "/api/projects/{project_id}/data-readiness",
    response_model=dict[str, object],
)
def get_project_data_readiness(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_data_readiness(project_id=project_id, store=store)


# BIDS validation


@router.get(
    "/api/projects/{project_id}/bids-validation",
    response_model=dict[str, object],
)
def get_project_bids_validation(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return validate_bids(project_id=project_id, store=store)


# BOLD reference readiness


@router.get(
    "/api/projects/{project_id}/bold-reference/readiness",
    response_model=dict[str, object],
)
def get_project_bold_reference_readiness(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_bold_reference_readiness(project_id=project_id, store=store)


# rs-fMRI QC planning


@router.post(
    "/api/projects/{project_id}/rsfmri-qc/planning-report",
    response_model=dict[str, object],
)
def post_rsfmri_qc_planning_report(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_rsfmri_qc_planning_report(project_id=project_id, store=store)


# Motion QC


@router.post(
    "/api/projects/{project_id}/motion-qc/metrics-draft",
    response_model=dict[str, object],
)
def post_motion_metrics_draft(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_motion_metrics_draft(project_id=project_id, store=store)


@router.get(
    "/api/projects/{project_id}/motion-qc/readiness",
    response_model=dict[str, object],
)
def get_project_motion_qc_readiness(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_motion_qc_readiness(project_id=project_id, store=store)


# SPM realign


@router.post(
    "/api/projects/{project_id}/spm-realign/dry-run",
    response_model=dict[str, object],
)
def post_spm_realign_dry_run(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_spm_realign_dry_run(project_id=project_id, store=store)


@router.post(
    "/api/projects/{project_id}/spm-realign/wrapper-skeleton",
    response_model=dict[str, object],
)
def post_spm_realign_wrapper_skeleton(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_spm_realign_wrapper_skeleton(project_id=project_id, store=store)


# NIfTI QC


@router.get(
    "/api/projects/{project_id}/nifti-qc/snapshot",
    response_model=dict[str, object],
)
def get_project_nifti_qc_snapshot(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_nifti_qc_snapshot(project_id=project_id, store=store)


@router.get(
    "/api/projects/{project_id}/nifti-qc/images/{image_id}/thumbnail",
    response_model=dict[str, object],
)
def get_project_nifti_thumbnail(
    project_id: str,
    image_id: str,
    view: str = Query(default="all"),
    volume_index: int | None = Query(default=None, ge=0),
    size: int | None = Query(default=None),
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    from fastapi import HTTPException

    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if view not in ("axial", "coronal", "sagittal", "all"):
        raise HTTPException(status_code=400, detail=f"Invalid view: {view}")
    try:
        return build_nifti_thumbnail(
            project_id=project_id,
            image_id=image_id,
            view=view,
            volume_index=volume_index,
            size=size,
            store=store,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
