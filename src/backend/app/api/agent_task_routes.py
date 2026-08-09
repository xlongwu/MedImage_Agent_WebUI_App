"""Thin HTTP adapter for the project-scoped Agent Task projection."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.agent_task_authorization import (
    require_agent_task_approval_principal,
)
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.schemas.agent_task import (
    AgentTaskEventPage,
    AgentTaskListResponse,
    AgentTaskResponse,
    AnswerAgentTaskRequest,
    ApproveAgentTaskRecoveryRequest,
    ApproveAgentTaskRequest,
    CancelAgentTaskRequest,
    CreateAgentTaskRequest,
)
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService
from src.backend.app.services.agent_task_read_model import AgentTaskReadModel

router = APIRouter(prefix="/api/projects/{project_id}/agent/tasks", tags=["agent-tasks"])


@router.post("", response_model=AgentTaskResponse)
def create_agent_task(
    project_id: str,
    request: CreateAgentTaskRequest,
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskResponse:
    try:
        lifecycle = AgentTaskCommandService(store).create(
            project_id=project_id,
            goal=request.goal,
            command_id=request.command_id,
            actor=request.actor,
        )
        return AgentTaskReadModel(store).get(project_id=project_id, task_id=lifecycle.lifecycle_id)
    except Exception as exc:
        raise_api_error(exc)


@router.post("/{task_id}/answer", response_model=AgentTaskResponse)
def answer_agent_task(
    project_id: str,
    task_id: str,
    request: AnswerAgentTaskRequest,
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskResponse:
    try:
        lifecycle = AgentTaskCommandService(store).answer(
            project_id=project_id,
            lifecycle_id=task_id,
            batch_id=request.batch_id,
            answers=request.answers,
            command_id=request.command_id,
            actor=request.actor,
        )
        return AgentTaskReadModel(store).get(project_id=project_id, task_id=lifecycle.lifecycle_id)
    except Exception as exc:
        raise_api_error(exc)


@router.post("/{task_id}/approve", response_model=AgentTaskResponse)
def approve_agent_task(
    project_id: str,
    task_id: str,
    request: ApproveAgentTaskRequest,
    approver: Annotated[str, Depends(require_agent_task_approval_principal)],
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskResponse:
    try:
        lifecycle = AgentTaskCommandService(store).approve(
            project_id=project_id,
            lifecycle_id=task_id,
            approval_summary_hash=request.approval_summary_hash,
            command_id=request.command_id,
            actor=approver,
        )
        return AgentTaskReadModel(store).get(project_id=project_id, task_id=lifecycle.lifecycle_id)
    except Exception as exc:
        raise_api_error(exc)


@router.post("/{task_id}/cancel", response_model=AgentTaskResponse)
def cancel_agent_task(
    project_id: str,
    task_id: str,
    request: CancelAgentTaskRequest,
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskResponse:
    try:
        lifecycle = AgentTaskCommandService(store).cancel(
            project_id=project_id,
            lifecycle_id=task_id,
            command_id=request.command_id,
            actor=request.actor,
            reason=request.reason,
        )
        return AgentTaskReadModel(store).get(project_id=project_id, task_id=lifecycle.lifecycle_id)
    except Exception as exc:
        raise_api_error(exc)


@router.post("/{task_id}/approve-recovery", response_model=AgentTaskResponse)
def approve_agent_task_recovery(
    project_id: str,
    task_id: str,
    request: ApproveAgentTaskRecoveryRequest,
    approver: Annotated[str, Depends(require_agent_task_approval_principal)],
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskResponse:
    try:
        lifecycle = AgentTaskCommandService(store).approve_recovery(
            project_id=project_id,
            lifecycle_id=task_id,
            command_id=request.command_id,
            actor=approver,
        )
        return AgentTaskReadModel(store).get(project_id=project_id, task_id=lifecycle.lifecycle_id)
    except Exception as exc:
        raise_api_error(exc)


@router.get("", response_model=AgentTaskListResponse)
def list_agent_tasks(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskListResponse:
    return AgentTaskReadModel(store).list(project_id=project_id)


@router.get("/{task_id}", response_model=AgentTaskResponse)
def get_agent_task(
    project_id: str,
    task_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskResponse:
    return AgentTaskReadModel(store).get(project_id=project_id, task_id=task_id)


@router.get("/{task_id}/events", response_model=AgentTaskEventPage)
def list_agent_task_events(
    project_id: str,
    task_id: str,
    after: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store: ProjectStore = Depends(get_project_store),
) -> AgentTaskEventPage:
    return AgentTaskReadModel(store).events(
        project_id=project_id,
        task_id=task_id,
        after=after,
        limit=limit,
    )
