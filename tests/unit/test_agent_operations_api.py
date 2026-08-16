from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.app.main import create_app


def test_operations_summary_rejects_invalid_window_without_side_effects() -> None:
    client = TestClient(create_app())
    response = client.get("/api/projects/unknown/agent-operations/summary?window_hours=0")
    assert response.status_code == 422
