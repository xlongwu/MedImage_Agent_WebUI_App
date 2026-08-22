from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api import dashboard_routes, project_routes
from src.backend.app.main import app
from src.backend.app.runtime import desktop_config
from src.backend.app.services import mock_store as mock_store_module
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    for module in (project_routes, dashboard_routes, mock_store_module):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(
    client: TestClient, tmp_path: Path, name: str = "Delete Recent Project"
) -> dict:
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    project_dir = tmp_path / "managed_project"
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": name,
            "rawdata_dir": str(rawdata),
            "project_dir": str(project_dir),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_delete_project_removes_recent_dashboard_record_without_deleting_files(
    tmp_path, monkeypatch
):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    project_id = created["project_id"]
    project_dir = Path(created["project_dir"])
    rawdata_dir = Path(created["rawdata_dir"])

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["deleted_files"] is False
    assert body["removed_from_store"] is True
    assert body["removed_from_recent"] is True
    assert project_dir.exists(), "Deleting a recent project must not remove managed project files"
    assert rawdata_dir.exists(), "Deleting a recent project must not remove rawdata"
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert project_id not in {item["id"] for item in client.get("/api/projects").json()}
    recent = desktop_config.get_desktop_config(redacted=False)["recent_projects"]
    assert project_id not in {item.get("project_id") for item in recent}


def test_delete_project_not_found_returns_404(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.delete("/api/projects/not-a-project")

    assert response.status_code == 404


def test_store_does_not_reseed_after_all_projects_are_removed(tmp_path):
    db_path = tmp_path / "desktop_state.sqlite"
    store = SQLiteDesktopStore(db_path)
    for project in list(store.list_projects()):
        assert store.remove_project(project.id) is True
    assert store.list_projects() == []

    reopened = SQLiteDesktopStore(db_path)

    assert reopened.list_projects() == []
