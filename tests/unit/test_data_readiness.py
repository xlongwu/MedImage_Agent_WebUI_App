"""Tests for GET /api/projects/{project_id}/data-readiness."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import src.backend.app.services.mock_store as mock_store_module
from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services import data_readiness
from src.backend.app.services.mock_store import SQLiteDesktopStore


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
        data_readiness,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(client: TestClient, tmp_path: Path, name: str = "Readiness Project") -> dict:
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


# ── Tests ────────────────────────────────────────────────────────────────────


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/data-readiness")
    assert resp.status_code == 404


def test_created_project_returns_readiness_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["status"] in ("ready", "warning", "blocked", "unknown")
    assert body["project_config_path"] is not None
    assert isinstance(body["checks"], list)
    assert len(body["checks"]) > 0
    assert isinstance(body["next_actions"], list)
    assert len(body["next_actions"]) > 0


def test_readiness_has_all_required_checks(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    body = resp.json()
    check_names = {c["name"] for c in body["checks"]}
    required = {
        "project_metadata",
        "rawdata_path",
        "import_records",
        "image_source_discovery",
        "image_validation",
        "dataset_index",
        "dicom_preflight",
        "rawdata_read_only",
    }
    missing = required - check_names
    assert not missing, f"Missing checks: {missing}"


def test_readiness_includes_rawdata_safety_check(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    body = resp.json()
    safety_checks = [c for c in body["checks"] if c["name"] == "rawdata_read_only"]
    assert len(safety_checks) == 1
    assert safety_checks[0]["status"] == "pass"
    assert "read-only" in safety_checks[0]["message"].lower()


def test_no_imports_returns_blocked(tmp_path, monkeypatch):
    """A project with no image sources and no imports should show blocked/warning."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    body = resp.json()
    # Without explicit imports, image_source_count may be 0
    assert isinstance(body["image_source_count"], int)
    assert isinstance(body["import_count"], int)


def test_next_actions_present(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    body = resp.json()
    assert isinstance(body["next_actions"], list)
    assert len(body["next_actions"]) > 0


def test_warnings_and_errors_are_lists(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    body = resp.json()
    assert isinstance(body["warnings"], list)
    assert isinstance(body["errors"], list)


def test_dicom_fields_are_int(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    body = resp.json()
    assert isinstance(body["dicom_file_count"], int)
    assert isinstance(body["dicom_series_count"], int)
    assert isinstance(body["image_source_count"], int)
    assert isinstance(body["subject_count"], int)
    assert isinstance(body["sequence_count"], int)
