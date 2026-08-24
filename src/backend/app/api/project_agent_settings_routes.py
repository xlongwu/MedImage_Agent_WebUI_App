from __future__ import annotations

from fastapi import APIRouter, Depends

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.schemas.project_agent_settings import (
    ProjectAgentSettings,
    UpdateProjectAgentSettingsRequest,
)
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService

router = APIRouter(prefix="/api/projects/{project_id}/agent-settings", tags=["agent-settings"])


@router.get("", response_model=ProjectAgentSettings)
def get_agent_settings(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> ProjectAgentSettings:
    try:
        return ProjectAgentSettingsService(store).get(project_id=project_id)
    except Exception as exc:
        raise_api_error(exc)


@router.put("", response_model=ProjectAgentSettings)
def update_agent_settings(
    project_id: str,
    request: UpdateProjectAgentSettingsRequest,
    store: ProjectStore = Depends(get_project_store),
) -> ProjectAgentSettings:
    try:
        return ProjectAgentSettingsService(store).update(project_id=project_id, request=request)
    except Exception as exc:
        raise_api_error(exc)
