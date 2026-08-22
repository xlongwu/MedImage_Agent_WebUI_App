"""Tests for GET /api/projects/{project_id}/runs/{run_id}/events and /logs."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.goal_contract_helpers import reviewed_goal_candidate


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(
        desktop_config,
        "DESKTOP_CONFIG_PATH",
        tmp_path / "desktop_config.json",
    )
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        execute_reviewed_routes,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(client: TestClient, tmp_path: Path, name: str = "Events Logs Project") -> dict:
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": name,
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / name.replace(" ", "_")),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _reviewed_plan(created: dict) -> dict:
    rawdata_dir = created["rawdata_dir"]
    dataset_index_path = created["dataset_index_path"]
    return {
        "pipeline_id": "events-logs-test-plan",
        "project_context": {
            "project_id": created["project_id"],
            "project_config_path": created["project_config_path"],
            "rawdata_dir": rawdata_dir,
            "dataset_index_path": dataset_index_path,
            "source": "created",
            "diagnostics": created["diagnostics"],
        },
        "nodes": [
            {
                "id": "contract_smoke",
                "backend": "python",
                "depends_on": [],
                "params": {},
            },
        ],
    }


def _save_plan(client: TestClient, created: dict, plan: dict) -> dict:
    goal = "Events and logs test"
    response = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True},
            "goal": goal,
            "provider": "mock",
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "test-reviewer",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["reviewed_plan"]


def _execute_plan(
    client: TestClient,
    created: dict,
    plan: dict,
    reviewed_plan_id: str,
) -> dict:
    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_by": "test"},
            "project_id": created["project_id"],
            "reviewed_plan_id": reviewed_plan_id,
            "project_config_path": created["project_config_path"],
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
            "actor": "test",
        },
    )
    return response.json()


# ── Event endpoint tests ────────────────────────────────────────────────────


def test_events_project_not_found():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/runs/any-run/events")
    assert resp.status_code == 404


def test_events_run_not_found(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/runs/nonexistent-run/events")
    assert resp.status_code == 404


def test_events_ok_after_real_execution(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id, f"No run_id in execute result: {result}"

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/events")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["run_id"] == run_id
    assert isinstance(body["events"], list)
    assert len(body["events"]) > 0, f"Expected events, got: {body}"


# ── Log endpoint tests ──────────────────────────────────────────────────────


def test_logs_project_not_found():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/runs/any-run/logs")
    assert resp.status_code == 404


def test_logs_run_not_found(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/runs/nonexistent-run/logs")
    assert resp.status_code == 404


def test_logs_ok_after_real_execution(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id, f"No run_id in execute result: {result}"

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/logs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["run_id"] == run_id
    assert isinstance(body["logs"], list)


def test_logs_respects_max_bytes(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/logs?max_bytes=2000")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for log_entry in body["logs"]:
        if log_entry.get("content") and not log_entry.get("truncated"):
            # content should be within max_bytes bounds
            assert len(log_entry["content"]) <= 2200, (
                f"Content too large: {len(log_entry['content'])} chars"
            )


def test_logs_endpoint_uses_only_project_run_scoping(tmp_path, monkeypatch):
    """The logs endpoint does not accept arbitrary file paths — it uses project/run scoping."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id

    # Passing an unexpected query param should be harmless (endpoint ignores it)
    resp = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/logs?path=../../etc/passwd"
    )
    assert resp.status_code == 200, resp.text
