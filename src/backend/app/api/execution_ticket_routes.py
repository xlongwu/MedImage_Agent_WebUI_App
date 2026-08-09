"""Read-only query surface for persisted execution authority and audit events."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.core.exceptions import NotFoundError

router = APIRouter(prefix="/api/projects/{project_id}/execution-tickets")


@router.get("")
def list_execution_tickets(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    if store.get_project(project_id) is None:
        raise NotFoundError(f"Project not found: {project_id}")
    return {
        "project_id": project_id,
        "execution_tickets": store.list_execution_tickets(project_id),
    }


@router.get("/{execution_ticket_id}")
def get_execution_ticket(
    project_id: str,
    execution_ticket_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    ticket = store.get_execution_ticket(execution_ticket_id)
    if ticket is None or ticket.project_id != project_id:
        raise NotFoundError(
            f"Execution ticket not found for project: {execution_ticket_id}"
        )
    dispatch = store.get_gateway_dispatch_by_ticket(execution_ticket_id)
    return {
        "project_id": project_id,
        "execution_ticket": ticket,
        "events": store.list_execution_ticket_events(execution_ticket_id),
        "dispatch": dispatch,
        "dispatch_events": (
            store.list_gateway_dispatch_events(dispatch.dispatch_id)
            if dispatch is not None
            else []
        ),
    }
