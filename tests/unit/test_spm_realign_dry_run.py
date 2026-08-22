"""Tests for POST /api/projects/{project_id}/spm-realign/dry-run."""

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
from src.backend.app.services import bold_reference_readiness, motion_qc_readiness
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        execute_reviewed_routes,
        bold_reference_readiness,
        motion_qc_readiness,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _create_project(client: TestClient, tmp_path: Path) -> dict:
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "DryRun Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "dryrun_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.post("/api/projects/nonexistent/spm-realign/dry-run")
    assert resp.status_code == 404


def test_created_project_returns_structured_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True or body["status"] == "blocked"
    assert body["dry_run"] is True
    assert body["node_id"] == "spm_realign_subject"
    assert isinstance(body["inputs"], list)
    assert isinstance(body["safety_flags"], dict)


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    flags = resp.json()["safety_flags"]
    for key in (
        "dry_run_only",
        "rawdata_not_modified",
        "no_files_created",
        "no_matlab_called",
        "no_spm_called",
        "execution_disabled",
        "approval_required",
        "audit_required",
    ):
        assert flags.get(key) is True


def test_execution_disabled(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    body = resp.json()
    assert body["execution_enabled"] is False
    assert body["safe_allowlist_enabled"] is False
    assert body["approval_required"] is True
    assert body["audit_required"] is True


def test_no_files_created(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    project_dir = Path(created["project_dir"])
    before = {str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()
    client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    after = {str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()
    assert after == before, f"Dry-run created files: {after - before}"


def test_predicted_output_paths_under_preview_root(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    body = resp.json()
    preview = body.get("output_root_preview")
    for inp in body.get("inputs", []):
        for out in inp.get("predicted_outputs", []):
            path_str = out.get("path", "")
            if preview and path_str:
                assert path_str.startswith(str(preview).replace("\\", "/")), (
                    f"Output {path_str} not under preview {preview}"
                )


def test_endpoint_ignores_arbitrary_path(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(
        f"/api/projects/{created['project_id']}/spm-realign/dry-run?path=../../etc",
    )
    assert resp.status_code == 200
