from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.main import create_app


def _attempt(**updates: object) -> SimpleNamespace:
    payload: dict[str, object] = {
        "sandbox_id": "sandbox-1",
        "project_id": "project-1",
        "run_id": "run-1",
        "node_id": "node-1",
        "subject_id": None,
        "status": "RUNNING",
        "started_at": None,
        "ended_at": None,
        "result_code": None,
        "output_count": 0,
        "network_isolation": "not_enforced",
        "command_hash": "must-not-be-public",
        "policy_hash": "must-not-be-public",
    }
    payload.update(updates)
    return SimpleNamespace(**payload)


class ReadOnlySandboxStore:
    def __init__(self, attempt: SimpleNamespace) -> None:
        self.attempt = attempt
        self.list_calls: list[tuple[str, str]] = []

    def get_run_link_by_run_id(self, project_id: str, run_id: str):
        if (project_id, run_id) in {("project-1", "run-1"), ("project-2", "run-2")}:
            return SimpleNamespace(project_id=project_id, run_id=run_id)
        return None

    def list_sandbox_attempts_for_run(self, project_id: str, run_id: str):
        self.list_calls.append((project_id, run_id))
        if self.attempt.project_id == project_id and self.attempt.run_id == run_id:
            return [self.attempt]
        return []

    def get_sandbox_attempt(self, sandbox_id: str):
        return self.attempt if sandbox_id == self.attempt.sandbox_id else None


def _client(store: ReadOnlySandboxStore) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_project_store] = lambda: store
    return TestClient(app)


def test_sandbox_attempt_list_is_project_scoped_redacted_and_read_only() -> None:
    store = ReadOnlySandboxStore(_attempt())
    response = _client(store).get("/api/projects/project-1/runs/run-1/sandbox-attempts")

    assert response.status_code == 200
    assert store.list_calls == [("project-1", "run-1")]
    payload = response.json()["sandbox_attempts"][0]
    assert payload["sandbox_id"] == "sandbox-1"
    assert payload["network_isolation"] == "not_enforced"
    assert "command_hash" not in payload
    assert "policy_hash" not in payload


def test_sandbox_attempt_detail_rejects_cross_project_reference() -> None:
    response = _client(ReadOnlySandboxStore(_attempt())).get(
        "/api/projects/project-2/runs/run-2/sandbox-attempts/sandbox-1"
    )

    assert response.status_code == 404


def test_sandbox_attempt_routes_require_a_registered_run() -> None:
    response = _client(ReadOnlySandboxStore(_attempt())).get(
        "/api/projects/project-1/runs/missing/sandbox-attempts"
    )

    assert response.status_code == 404
