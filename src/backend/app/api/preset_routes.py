"""Pipeline preset routes — read-only preset listing, detail, and instantiation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.backend.app.api.dependencies import ProjectStore, get_project_store

from src.backend.app.planner.pipeline_presets import (
    get_preset,
    instantiate_preset,
    list_presets,
)
from src.backend.app.schemas.preset_schemas import (
    PipelinePresetInstantiateRequest,
    PipelinePresetInstantiateResponse,
)

router = APIRouter()


@router.get("/api/pipeline-presets")
def api_list_pipeline_presets() -> dict[str, Any]:
    """List all available pipeline presets."""
    return {
        "ok": True,
        "presets": list_presets(),
    }


@router.get("/api/pipeline-presets/{preset_id}")
def api_get_pipeline_preset(preset_id: str) -> dict[str, Any]:
    """Get a single pipeline preset by id."""
    preset = get_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Preset not found: {preset_id}")
    return {"ok": True, "preset": preset.model_dump()}


@router.post(
    "/api/projects/{project_id}/pipeline-presets/{preset_id}/instantiate",
    response_model=PipelinePresetInstantiateResponse,
)
def api_instantiate_pipeline_preset(
    project_id: str,
    preset_id: str,
    request: PipelinePresetInstantiateRequest = PipelinePresetInstantiateRequest(),
    store: ProjectStore = Depends(get_project_store),
) -> PipelinePresetInstantiateResponse:
    """Instantiate a pipeline preset into a reviewed-plan-compatible plan dict."""
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    result = instantiate_preset(preset_id, request)
    result.project_id = project_id
    return result
