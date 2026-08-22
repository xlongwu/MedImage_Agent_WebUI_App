"""Read-only project-level Agent operational health endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.api.memory_dependencies import (
    MemoryStore,
    get_memory_config,
    get_readonly_memory_store,
)
from src.backend.app.schemas.agent_operations import AgentOperationalSummary
from src.backend.app.services.agent_operational_summary_service import AgentOperationalSummaryService

router = APIRouter(prefix="/api/projects/{project_id}/agent-operations", tags=["agent-operations"])


@router.get("/summary", response_model=AgentOperationalSummary)
def get_agent_operational_summary(
    project_id: str,
    window_hours: Annotated[int, Query(ge=1, le=720)] = 168,
    store: ProjectStore = Depends(get_project_store),
    memory_repository: MemoryStore = Depends(get_readonly_memory_store),
    memory_config=Depends(get_memory_config),
) -> AgentOperationalSummary:
    try:
        return AgentOperationalSummaryService(
            store,
            memory_repository=memory_repository,
            memory_config=memory_config,
        ).build(
            project_id=project_id, window_hours=window_hours,
        )
    except Exception as exc:
        raise_api_error(exc)
