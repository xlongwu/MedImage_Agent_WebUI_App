"""Image domain routes — extracted from dashboard_routes.py.

All endpoints mirror the original behavior; the only change is store access
via ``Depends(get_project_store)`` instead of the module-level ``mock_store``.
The canonical routes live here; ``dashboard_routes.py`` only retains helper
functions still used by characterization tests.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.schemas.desktop import (
    ImagePlane,
)
from src.backend.app.services.artifact_adapter import (
    build_image_preview,
    build_image_validation_report,
    list_image_sources,
)

router = APIRouter()

@router.get(
    "/api/images/preview",
    response_model=dict[str, object],
)
def image_preview(
    project_id: str = Query(...),
    subject_id: str | None = Query(default=None),
    sequence: str = Query(default="T1"),
    slice_index: int | None = Query(default=None, ge=0),
    plane: ImagePlane = Query(default="axial"),
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_image_preview(
        project_id=project_id,
        subject_id=subject_id,
        sequence=sequence,
        slice_index=slice_index,
        plane=plane,
        store=store,
    )


@router.get(
    "/api/images/sources",
    response_model=dict[str, object],
)
def image_sources(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return list_image_sources(project_id=project_id, store=store)


@router.get(
    "/api/images/manifest",
    response_model=dict[str, object],
)
def image_manifest(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return list_image_sources(project_id=project_id, store=store)


@router.get(
    "/api/images/validation",
    response_model=dict[str, object],
)
def image_validation(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    project = store.get_project(project_id)
    if not project:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_image_validation_report(project_id=project_id, store=store)
