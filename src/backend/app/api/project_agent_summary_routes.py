from __future__ import annotations

from fastapi import APIRouter, Depends

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.schemas.desktop import ProjectSummary
from src.backend.app.services.project_agent_summary_service import ProjectAgentSummaryService

router = APIRouter(prefix="/api/agent/projects", tags=["agent-projects"])


@router.get("", response_model=list[ProjectSummary])
def list_agent_projects(
    store: ProjectStore = Depends(get_project_store),
) -> list[ProjectSummary]:
    try:
        return ProjectAgentSummaryService(store).list_projects()
    except Exception as exc:
        raise_api_error(exc)
