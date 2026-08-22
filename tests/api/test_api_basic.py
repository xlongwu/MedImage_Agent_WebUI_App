from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.app.main import app


def test_health_api():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True


def test_pipelines_api():
    client = TestClient(app)
    response = client.get("/api/pipelines")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "pipelines" in payload


def test_path_traversal_rejected():
    client = TestClient(app)
    response = client.get("/api/files/read", params={"path": "../../etc/passwd"})

    assert response.status_code in {400, 403}


def test_removed_planner_draft_api_returns_not_found():
    client = TestClient(app)
    response = client.post(
        "/api/planner/draft",
        json={"downstream_task": "ALFF analysis", "disease_type": "AD"},
    )

    assert response.status_code == 404


def test_desktop_health_api():
    client = TestClient(app)
    response = client.get("/api/desktop/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "checks" in payload
