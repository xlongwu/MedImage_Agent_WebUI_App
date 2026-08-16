"""Read-only reviewed-plan execution graph routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.schemas.execution_graph import ExecutionGraphResponse
from src.backend.app.services.execution_graph_service import ExecutionGraphService

router = APIRouter(tags=["execution-graph"])


class ExecutionGraphPreviewRequest(BaseModel):
    plan: dict[str, Any]


@router.post("/api/projects/{project_id}/plan-graph-preview", response_model=ExecutionGraphResponse)
def preview_execution_graph(project_id: str, request: ExecutionGraphPreviewRequest, store: ProjectStore = Depends(get_project_store)) -> ExecutionGraphResponse:
    try:
        return ExecutionGraphService(store).build_preview_graph(project_id=project_id, plan=request.plan)
    except Exception as exc:
        raise_api_error(exc)


@router.get("/api/projects/{project_id}/plans/{reviewed_plan_id}/graph", response_model=ExecutionGraphResponse)
def get_plan_execution_graph(project_id: str, reviewed_plan_id: str, store: ProjectStore = Depends(get_project_store)) -> ExecutionGraphResponse:
    try:
        return ExecutionGraphService(store).build_plan_graph(project_id=project_id, reviewed_plan_id=reviewed_plan_id)
    except Exception as exc:
        raise_api_error(exc)


@router.get("/api/projects/{project_id}/runs/{run_id}/graph", response_model=ExecutionGraphResponse)
def get_run_execution_graph(project_id: str, run_id: str, store: ProjectStore = Depends(get_project_store)) -> ExecutionGraphResponse:
    try:
        return ExecutionGraphService(store).build_run_graph(project_id=project_id, run_id=run_id)
    except Exception as exc:
        raise_api_error(exc)
