"""Project-scoped reviewed plan and execution history APIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.planner.reviewed_plan_store import (
    ReviewedPlanStoreError,
    artifact_warnings,
    save_reviewed_plan,
    snapshot_warnings,
)
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord, RunLinkRecord
from src.backend.app.schemas.goal_contract import GoalContractCandidate
from src.backend.app.schemas.planner_provenance import PlannerEvidence, PlannerInvocation
from src.backend.app.schemas.planning import PlanningRequest
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.run_artifact_discovery import (
    discover_run_artifacts,
    find_run_artifact,
)
from src.backend.app.services.run_artifact_preview import artifact_preview_payload
from src.backend.app.services.run_event_log_reader import (
    discover_run_events,
    discover_run_logs,
)
from src.backend.app.services.run_state_timeline import build_run_state_timeline
from src.backend.app.services.run_summary_preview import load_run_summary_preview
from src.backend.app.tools.artifact_utils import is_safe_artifact_id

router = APIRouter()


def get_project_history_store(
    store: ProjectStore = Depends(get_project_store),
) -> ProjectStore:
    return store


class ReviewedPlanSaveRequest(BaseModel):
    plan: dict[str, Any]
    project_config_path: str | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    goal: str | None = None
    provider: str | None = None
    status: str = "REVIEWED"
    warnings: list[str] = Field(default_factory=list)
    goal_contract_candidate: GoalContractCandidate | None = None
    reviewed_actor: str | None = None
    planner_invocation: PlannerInvocation | None = None
    planner_evidence: PlannerEvidence | None = None
    lifecycle_id: str | None = None


def _ensure_project(project_id: str, store: ProjectStore) -> None:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")


def _get_project(project_id: str, store: ProjectStore) -> ProjectDetail:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return project


def _reviewed_plan_payload(record: ReviewedPlanRecord) -> dict[str, Any]:
    payload = record.model_dump()
    payload["warnings"] = snapshot_warnings(record)
    return payload


def _run_link_payload(record: RunLinkRecord) -> dict[str, Any]:
    payload = record.model_dump()
    payload["warnings"] = artifact_warnings(record)
    return payload


def _dedupe(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(message for message in messages if message))


@router.post("/api/projects/{project_id}/plans")
def save_project_reviewed_plan(
    project_id: str,
    request: ReviewedPlanSaveRequest,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    _ensure_project(project_id, store)
    try:
        planning_request = None
        if request.lifecycle_id:
            lifecycle = store.get_agent_lifecycle(request.lifecycle_id)
            if lifecycle is None or lifecycle.project_id != project_id:
                raise ReviewedPlanStoreError("PLANNING_REQUEST_LIFECYCLE_MISMATCH")
            if lifecycle.state not in {"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"}:
                raise ReviewedPlanStoreError("PLANNING_REQUEST_LIFECYCLE_STATE_INVALID")
            snapshot = AgentEvidenceService(store).build_snapshot(
                project_id=project_id,
                lifecycle_id=request.lifecycle_id,
                requested_types=("project", "dataset", "artifacts", "plans", "capabilities"),
            )
            planning_request = PlanningRequest(
                project_id=project_id,
                lifecycle_id=request.lifecycle_id,
                goal=str(request.goal or ""),
                project_config_path=str(request.project_config_path or ""),
                evidence_snapshot_hash=snapshot.snapshot_hash,
                revision_reason="initial",
                provider_ref=str(request.provider or "manual-reviewed-plan"),
                prompt_version="manual-reviewed-plan-v1",
                model_profile_hash=stable_hash(
                    {
                        "provider": str(request.provider or "manual-reviewed-plan"),
                        "prompt_version": "manual-reviewed-plan-v1",
                    }
                ),
            )
        record = save_reviewed_plan(
            project_id=project_id,
            project_config_path=request.project_config_path,
            plan=request.plan,
            validation=request.validation,
            goal=request.goal,
            provider=request.provider,
            status=request.status,
            warnings=request.warnings,
            goal_contract_candidate=request.goal_contract_candidate,
            reviewed_actor=request.reviewed_actor,
            planner_invocation=request.planner_invocation,
            planner_evidence=request.planner_evidence,
            planning_request=planning_request,
            store=store,
        )
    except ReviewedPlanStoreError as exc:
        raise_api_error(exc)
    return {"ok": True, "reviewed_plan": _reviewed_plan_payload(record)}


@router.get("/api/projects/{project_id}/plans")
def list_project_reviewed_plans(
    project_id: str,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    _ensure_project(project_id, store)
    return {
        "ok": True,
        "project_id": project_id,
        "reviewed_plans": [
            _reviewed_plan_payload(record)
            for record in store.list_reviewed_plans(project_id)
        ],
    }


@router.get("/api/projects/{project_id}/plans/{reviewed_plan_id}")
def get_project_reviewed_plan(
    project_id: str,
    reviewed_plan_id: str,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    _ensure_project(project_id, store)
    record = store.get_reviewed_plan(reviewed_plan_id)
    if record is None or record.project_id != project_id:
        raise HTTPException(status_code=404, detail="Reviewed plan not found")
    return {"ok": True, "reviewed_plan": _reviewed_plan_payload(record)}


@router.get("/api/projects/{project_id}/runs")
def list_project_run_links(
    project_id: str,
    reviewed_plan_id: str | None = Query(default=None),
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    _ensure_project(project_id, store)
    return {
        "ok": True,
        "project_id": project_id,
        "runs": [
            _run_link_payload(record)
            for record in store.list_run_links(project_id, reviewed_plan_id)
        ],
    }


@router.get("/api/projects/{project_id}/runs/{run_id}")
def get_project_run_link(
    project_id: str,
    run_id: str,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    project = _get_project(project_id, store)
    record = store.get_run_link_by_run_id(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run link not found")
    run_link = _run_link_payload(record)
    summary_preview, summary_warnings, summary_error = load_run_summary_preview(
        project,
        record,
    )
    warnings = _dedupe(
        [
            *run_link.get("warnings", []),
            *summary_warnings,
            *([summary_error] if summary_error else []),
        ]
    )
    return {
        "ok": True,
        "run_link": run_link,
        "summary_preview": summary_preview,
        "summary_preview_error": summary_error,
        "warnings": warnings,
    }


@router.get("/api/projects/{project_id}/runs/{run_id}/artifacts")
def list_project_run_artifacts(
    project_id: str,
    run_id: str,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    project = _get_project(project_id, store)
    record = store.get_run_link_by_run_id(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run link not found")
    artifacts, warnings = discover_run_artifacts(project, record)
    return {
        "ok": True,
        "project_id": project_id,
        "run_id": run_id,
        "artifacts": artifacts,
        "warnings": warnings,
    }


@router.get("/api/projects/{project_id}/runs/{run_id}/artifacts/{artifact_id}")
def get_project_run_artifact(
    project_id: str,
    run_id: str,
    artifact_id: str,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    if not is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=400, detail="Invalid artifact_id")
    project = _get_project(project_id, store)
    record = store.get_run_link_by_run_id(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run link not found")
    artifact, warnings = find_run_artifact(project, record, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found for run")
    payload = artifact_preview_payload(artifact)
    payload["project_id"] = project_id
    payload["run_id"] = run_id
    payload["warnings"] = _dedupe([*warnings, *payload.get("warnings", [])])
    return payload


@router.get("/api/projects/{project_id}/runs/{run_id}/events")
def list_project_run_events(
    project_id: str,
    run_id: str,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    project = _get_project(project_id, store)
    record = store.get_run_link_by_run_id(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run link not found")
    events, warnings = discover_run_events(project, record)
    return {
        "ok": True,
        "project_id": project_id,
        "run_id": run_id,
        "events": events,
        "warnings": warnings,
        "errors": [],
    }


@router.get("/api/projects/{project_id}/runs/{run_id}/logs")
def list_project_run_logs(
    project_id: str,
    run_id: str,
    max_bytes: int = Query(default=20000, ge=1000, le=200000),
    include_content: bool = Query(default=True),
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    project = _get_project(project_id, store)
    record = store.get_run_link_by_run_id(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run link not found")
    logs, warnings, errors = discover_run_logs(
        project,
        record,
        max_bytes=max_bytes,
        include_content=include_content,
    )
    return {
        "ok": True,
        "project_id": project_id,
        "run_id": run_id,
        "logs": logs,
        "warnings": warnings,
        "errors": errors,
    }


@router.get("/api/projects/{project_id}/runs/{run_id}/state-timeline")
def get_project_run_state_timeline(
    project_id: str,
    run_id: str,
    store: ProjectStore = Depends(get_project_history_store),
) -> dict[str, Any]:
    """Return a standardized run-state timeline using Phase 3 state model.

    Read-only — never modifies executor state, writes files, or calls
    external tools.
    """
    project = _get_project(project_id, store)
    record = store.get_run_link_by_run_id(project_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run link not found")

    # Gather existing metadata
    summary_preview, _, _ = load_run_summary_preview(project, record)

    events: list[dict[str, Any]] = []
    try:
        from src.backend.app.services.run_event_log_reader import discover_run_events
        events, _ = discover_run_events(project, record)
    except Exception:
        pass

    # Discover normalized node-state artifacts
    node_states: list[dict[str, Any]] = []
    try:
        from src.backend.app.services.run_artifact_discovery import discover_run_artifacts
        artifacts, _ = discover_run_artifacts(project, record)
        for art in artifacts:
            name = str(art.get("name") or "")
            if art.get("artifact_type") != "node_state" and "node_state" not in name.lower():
                continue
            if not art.get("exists"):
                continue
            try:
                raw = json.loads(
                    Path(str(art["path"])).read_text(encoding="utf-8")
                )
                if isinstance(raw, dict):
                    node_states.append(raw)
            except Exception:
                pass
    except Exception:
        pass

    timeline = build_run_state_timeline(
        project_id=project_id,
        run_id=run_id,
        run_link_status=record.status,
        created_at=record.created_at,
        summary_preview=summary_preview,
        run_events=list(events) if events else None,
        node_states_raw=node_states if node_states else None,
    )
    return timeline.model_dump()
