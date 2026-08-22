"""Task domain routes — extracted from dashboard_routes.py.

All endpoints mirror the original behavior; the only change is store access
via ``Depends(get_project_store)`` instead of the module-level ``mock_store``.
The canonical routes live here; ``dashboard_routes.py`` only retains helper
functions still used by characterization tests.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.api.execution_contract import reject_execution_contract
from src.backend.app.schemas.desktop import (
    AssistantChatRequest,
    AssistantChatResponse,
    PipelineRunRequest,
    TaskApprovalRequest,
)
from src.backend.app.services.assistant_service import build_assistant_reply
from src.backend.app.services.task_adapter import (
    approve_task,
    generate_task_audit_package,
    get_task,
    get_task_artifacts,
    get_task_diagnostics,
    list_task_events,
    list_tasks,
)

router = APIRouter()

# Task listing and detail


@router.get(
    "/api/tasks",
    response_model=list[dict[str, object]],
)
def list_tasks_endpoint(
    store: ProjectStore = Depends(get_project_store),
) -> list[dict[str, object]]:
    return list_tasks(store=store)


@router.get(
    "/api/tasks/{task_id}",
    response_model=dict[str, object],
)
def get_task_endpoint(
    task_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    return get_task(task_id=task_id, store=store)


@router.get(
    "/api/tasks/{task_id}/events",
    response_model=list[dict[str, object]],
)
def get_task_events_endpoint(
    task_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> list[dict[str, object]]:
    return list_task_events(task_id=task_id, store=store)


@router.post(
    "/api/tasks/{task_id}/approve",
    response_model=dict[str, object],
)
async def approve_task_endpoint(
    task_id: str,
    request: TaskApprovalRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    return await approve_task(
        task_id=task_id,
        request=request.model_dump(),
        store=store,
    )


# Task diagnostics and artifacts


@router.get(
    "/api/tasks/{task_id}/diagnostics",
    response_model=dict[str, object],
)
def get_task_diagnostics_endpoint(
    task_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    return get_task_diagnostics(task_id=task_id, store=store)


@router.get(
    "/api/tasks/{task_id}/artifacts",
    response_model=dict[str, object],
)
def get_task_artifacts_endpoint(
    task_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    return get_task_artifacts(task_id=task_id, store=store)


@router.post(
    "/api/tasks/{task_id}/audit-package",
    response_model=dict[str, object],
)
def generate_task_audit_package_endpoint(
    task_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    return generate_task_audit_package(task_id=task_id, store=store)


# Pipeline execution


@router.post(
    "/api/pipelines/run",
    response_model=dict[str, object],
)
async def run_pipeline(
    request: PipelineRunRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    import asyncio

    from fastapi import HTTPException

    from src.backend.app.services.pipeline_runner import run_pipeline_task
    from src.backend.app.services.task_manager import task_manager

    if request.execution_mode != "simulated":
        reject_execution_contract("pipeline.task", project_id=request.project_id)

    if not request.input_sequences:
        raise HTTPException(status_code=400, detail="input_sequences must not be empty")

    if (
        request.execution_mode == "external_smoke"
        and request.external_smoke_mode == "approved_smoke"
    ):
        if not request.approved:
            raise HTTPException(
                status_code=403, detail="approved=true is required for approved_smoke"
            )
        if not (request.approved_by or "").strip():
            raise HTTPException(
                status_code=400, detail="approved_by is required for approved_smoke"
            )

    try:
        task = task_manager.create_pipeline_task(request)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {request.project_id}"
        ) from exc  # type: ignore[return-value]

    if (
        request.execution_mode == "external_smoke"
        and request.external_smoke_mode == "approved_smoke"
    ):
        approval = store.add_approval(
            task.id,
            approved=True,
            approved_by=(request.approved_by or "").strip(),
            safety_flags={
                "rawdata_read_only": True,
                "no_dparsf_blackbox": True,
                "matlab_external_execution": True,
            },
        )
        store.append_task_event(
            task.id,
            status=task.status,
            progress=task.progress,
            message=f"Run-level approval recorded by {approval.approved_by}",
            source="approval_gate",
            metadata={"approval_id": approval.approval_id},
        )
    asyncio.create_task(run_pipeline_task(task.id, request, task_manager))
    return {"task_id": task.id, "status": task.status}


# Assistant chat


@router.post(
    "/api/assistant/chat",
    response_model=AssistantChatResponse,
)
def assistant_chat(
    request: AssistantChatRequest,
    store: ProjectStore = Depends(get_project_store),
) -> AssistantChatResponse:
    reply = build_assistant_reply(
        store=store,
        project_id=request.project_id,
        message=request.message,
    )
    if reply is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Project not found: {request.project_id}")
    return AssistantChatResponse(reply=reply)
