from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.main import app
from src.backend.app.schemas.agent_trace import AgentTracePage
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService
from src.backend.app.services.agent_trace_service import AgentTraceService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.unit.test_agent_task_read_model import ReadOnlyStore


def test_agent_task_get_endpoints_are_project_scoped_and_read_only() -> None:
    store = ReadOnlyStore()
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        client = TestClient(app)

        listed = client.get("/api/projects/project-1/agent/tasks")
        detail = client.get("/api/projects/project-1/agent/tasks/task-1")
        events = client.get("/api/projects/project-1/agent/tasks/task-1/events?limit=10")
        crossed = client.get("/api/projects/project-2/agent/tasks/task-1")

        assert listed.status_code == 200
        assert listed.json()["items"][0]["task_id"] == "task-1"
        assert detail.status_code == 200
        assert detail.json()["state"] == "waiting_for_user"
        assert "decisions" not in detail.json()
        assert events.status_code == 200
        assert events.json() == {"schema_version": 1, "items": [], "next_cursor": None}
        assert crossed.status_code == 404
        assert store.write_calls == []
    finally:
        app.dependency_overrides.pop(get_project_store, None)


def test_agent_task_events_reject_invalid_limit() -> None:
    store = ReadOnlyStore()
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        response = TestClient(app).get("/api/projects/project-1/agent/tasks/task-1/events?limit=0")
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_project_store, None)


def test_agent_task_trace_is_a_paginated_read_only_advanced_projection(monkeypatch) -> None:
    store = ReadOnlyStore()
    captured: dict[str, object] = {}

    def page(self, **kwargs):
        captured.update(kwargs)
        return AgentTracePage(
            trace_id="agent_trace:task-1", project_id="project-1", lifecycle_id="task-1",
            integrity_status="complete", integrity_hash="trace-hash", final_state="CREATED",
            entries=(), next_cursor=None,
        )

    monkeypatch.setattr(AgentTraceService, "page", page)
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        response = TestClient(app).get(
            "/api/projects/project-1/agent/tasks/task-1/trace?after=0&limit=10"
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)

    assert response.status_code == 200
    assert response.json()["integrity_hash"] == "trace-hash"
    assert captured == {"project_id": "project-1", "lifecycle_id": "task-1", "after": 0, "limit": 10}
    assert store.write_calls == []


def test_agent_task_harness_is_a_paginated_read_only_advanced_projection(monkeypatch) -> None:
    store = ReadOnlyStore()
    captured: dict[str, object] = {}

    def page(self, **kwargs):
        captured.update(kwargs)
        return AgentTracePage(
            trace_id="agent_trace:task-1", project_id="project-1", lifecycle_id="task-1",
            integrity_status="complete", integrity_hash="trace-hash", final_state="CREATED",
            entries=(), next_cursor=None,
        )

    monkeypatch.setattr(AgentTraceService, "page", page)
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        response = TestClient(app).get(
            "/api/projects/project-1/agent/tasks/task-1/harness?after=0&limit=10"
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)

    assert response.status_code == 200
    assert response.json()["integrity_hash"] == "trace-hash"
    assert captured == {"project_id": "project-1", "lifecycle_id": "task-1", "after": 0, "limit": 10}
    assert store.write_calls == []


def test_agent_task_create_command_is_project_scoped_and_returns_projection(monkeypatch) -> None:
    store = ReadOnlyStore()
    captured = {}

    def create(self, **kwargs):
        captured.update(kwargs)
        return store.lifecycle

    monkeypatch.setattr(AgentTaskCommandService, "create", create)
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        response = TestClient(app).post(
            "/api/projects/project-1/agent/tasks",
            json={"goal": "Compute FC", "command_id": "create-api-1", "actor": "local-user"},
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-1"
    assert captured == {
        "project_id": "project-1",
        "goal": "Compute FC",
        "command_id": "create-api-1",
        "actor": "local-user",
    }


def test_agent_task_approval_requires_server_authorization_and_rejects_client_actor(monkeypatch) -> None:
    store = ReadOnlyStore()
    app.dependency_overrides[get_project_store] = lambda: store
    monkeypatch.setenv("MEDIMAGE_AGENT_APPROVAL_TOKEN", "test-approval-token")
    monkeypatch.setenv("MEDIMAGE_AGENT_APPROVAL_ACTOR", "trusted-desktop-user")
    try:
        client = TestClient(app)
        rejected = client.post(
            "/api/projects/project-1/agent/tasks/task-1/approve",
            json={
                "approval_summary_hash": "hash",
                "command_id": "approve-api-1",
            },
        )
        malformed = client.post(
            "/api/projects/project-1/agent/tasks/task-1/approve",
            headers={"X-MedImage-Agent-Approval-Token": "test-approval-token"},
            json={
                "approval_summary_hash": "hash",
                "command_id": "approve-api-2",
                "actor": "spoofed-user",
            },
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "AGENT_APPROVAL_AUTH_REQUIRED"
    assert malformed.status_code == 422


def test_agent_task_approval_derives_actor_from_authorized_server_context(monkeypatch) -> None:
    store = ReadOnlyStore()
    captured = {}

    def approve(self, **kwargs):
        captured.update(kwargs)
        return store.lifecycle

    monkeypatch.setattr(AgentTaskCommandService, "approve", approve)
    monkeypatch.setenv("MEDIMAGE_AGENT_APPROVAL_TOKEN", "test-approval-token")
    monkeypatch.setenv("MEDIMAGE_AGENT_APPROVAL_ACTOR", "trusted-desktop-user")
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        response = TestClient(app).post(
            "/api/projects/project-1/agent/tasks/task-1/approve",
            headers={"X-MedImage-Agent-Approval-Token": "test-approval-token"},
            json={"approval_summary_hash": "hash", "command_id": "approve-api-3"},
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)

    assert response.status_code == 200
    assert captured["actor"] == "trusted-desktop-user"


def test_agent_task_recovery_approval_uses_server_authorization(monkeypatch) -> None:
    store = ReadOnlyStore()
    captured = {}

    def approve_recovery(self, **kwargs):
        captured.update(kwargs)
        return store.lifecycle

    monkeypatch.setattr(AgentTaskCommandService, "approve_recovery", approve_recovery)
    monkeypatch.setenv("MEDIMAGE_AGENT_APPROVAL_TOKEN", "test-approval-token")
    monkeypatch.setenv("MEDIMAGE_AGENT_APPROVAL_ACTOR", "trusted-desktop-user")
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        client = TestClient(app)
        rejected = client.post(
            "/api/projects/project-1/agent/tasks/task-1/approve-recovery",
            json={"command_id": "approve-recovery-api-1"},
        )
        approved = client.post(
            "/api/projects/project-1/agent/tasks/task-1/approve-recovery",
            headers={"X-MedImage-Agent-Approval-Token": "test-approval-token"},
            json={"command_id": "approve-recovery-api-2"},
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "AGENT_APPROVAL_AUTH_REQUIRED"
    assert approved.status_code == 200
    assert captured["actor"] == "trusted-desktop-user"


def test_agent_task_gets_do_not_change_sqlite_or_file_inventory(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "agent-task.sqlite")
    store.add_project(
        ProjectDetail(
            id="project-1",
            name="Research cohort",
            study_id="study-1",
            modality="rs-fMRI",
            created_date="2026-07-16",
            subjects_count=1,
            current_pipeline_id="pipeline-1",
            sequences=[],
            scans_count=1,
            total_size="1 MB",
            current_model_id="none",
        ),
        health_status="Ready",
        rawdata_dir="",
    )
    lifecycle = AgentOrchestrator(store).create(
        project_id="project-1",
        command_id="create-readonly-fixture",
        actor="test",
    )

    def snapshot() -> tuple[dict[str, int], tuple[str, ...]]:
        tables = (
            "agent_lifecycles",
            "agent_lifecycle_events",
            "reviewed_plans",
            "execution_tickets",
            "observations",
            "goal_evaluations",
            "recovery_diagnoses",
            "recovery_proposals",
            "recovery_approvals",
            "recovery_attempts",
        )
        with sqlite3.connect(store.db_path) as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
        files = tuple(sorted(path.name for path in tmp_path.iterdir()))
        return counts, files

    before = snapshot()
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        client = TestClient(app)
        assert client.get("/api/projects/project-1/agent/tasks").status_code == 200
        assert (
            client.get(f"/api/projects/project-1/agent/tasks/{lifecycle.lifecycle_id}").status_code
            == 200
        )
        assert (
            client.get(
                f"/api/projects/project-1/agent/tasks/{lifecycle.lifecycle_id}/events"
            ).status_code
            == 200
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)

    assert snapshot() == before
