"""Read-only projection of gateway-created sandbox attempts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.backend.app.api.dependencies import ProjectStore, get_project_store

router = APIRouter(tags=["sandbox"])


def _public(attempt) -> dict[str, object]:
    return {
        "sandbox_id": attempt.sandbox_id,
        "run_id": attempt.run_id,
        "node_id": attempt.node_id,
        "subject_id": attempt.subject_id,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "ended_at": attempt.ended_at,
        "result_code": attempt.result_code,
        "output_count": attempt.output_count,
        "policy_version": "windows-sandbox-v1",
        "network_isolation": attempt.network_isolation,
    }


def _require_run(store: ProjectStore, project_id: str, run_id: str) -> None:
    if store.get_run_link_by_run_id(project_id, run_id) is None:
        raise HTTPException(status_code=404, detail="Run link not found")


@router.get("/api/projects/{project_id}/runs/{run_id}/sandbox-attempts")
def list_sandbox_attempts(project_id: str, run_id: str, store: ProjectStore = Depends(get_project_store)) -> dict[str, object]:
    _require_run(store, project_id, run_id)
    return {
        "ok": True,
        "project_id": project_id,
        "run_id": run_id,
        "sandbox_attempts": [_public(item) for item in store.list_sandbox_attempts_for_run(project_id, run_id)],
    }


@router.get("/api/projects/{project_id}/runs/{run_id}/sandbox-attempts/{sandbox_id}")
def get_sandbox_attempt(project_id: str, run_id: str, sandbox_id: str, store: ProjectStore = Depends(get_project_store)) -> dict[str, object]:
    _require_run(store, project_id, run_id)
    attempt = store.get_sandbox_attempt(sandbox_id)
    if attempt is None or attempt.project_id != project_id or attempt.run_id != run_id:
        raise HTTPException(status_code=404, detail="Sandbox attempt not found")
    return {"ok": True, "project_id": project_id, "run_id": run_id, "sandbox_attempt": _public(attempt)}
