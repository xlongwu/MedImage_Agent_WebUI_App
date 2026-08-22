from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.api.memory_dependencies import (
    get_memory_config,
    get_readonly_memory_store,
)
from src.backend.app.core.config_schema import MemoryConfig
from src.backend.app.main import create_app
from src.backend.app.services.mock_store import SQLiteDesktopStore


def test_operations_summary_rejects_invalid_window_without_side_effects() -> None:
    client = TestClient(create_app())
    response = client.get("/api/projects/unknown/agent-operations/summary?window_hours=0")
    assert response.status_code == 422


def test_operations_summary_is_read_only_and_structured(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "operations.sqlite")
    project_id = store.list_projects()[0].id
    app = create_app()
    app.dependency_overrides[get_project_store] = lambda: store
    app.dependency_overrides[get_readonly_memory_store] = lambda: object()
    app.dependency_overrides[get_memory_config] = lambda: MemoryConfig(
        enabled=False,
        generation_enabled=False,
        use_enabled=False,
        store_path=str(tmp_path / "does-not-exist.sqlite"),
    )

    response = TestClient(app).get(
        f"/api/projects/{project_id}/agent-operations/summary?window_hours=168"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["project_id"] == project_id
    assert payload["task_counts"] == {"total": 0}
    assert payload["attention"] == []
    assert not (tmp_path / "does-not-exist.sqlite").exists()


def test_operations_summary_returns_structured_not_found(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "operations.sqlite")
    app = create_app()
    app.dependency_overrides[get_project_store] = lambda: store
    app.dependency_overrides[get_readonly_memory_store] = lambda: object()
    app.dependency_overrides[get_memory_config] = lambda: MemoryConfig(
        enabled=False,
        generation_enabled=False,
        use_enabled=False,
        store_path=str(tmp_path / "does-not-exist.sqlite"),
    )

    response = TestClient(app).get("/api/projects/missing/agent-operations/summary")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_OP_PROJECT_NOT_FOUND"
