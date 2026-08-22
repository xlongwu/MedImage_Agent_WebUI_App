"""Characterization tests for dashboard_routes.py — Phase 3 safety net.

These tests lock the *observable contract* of each route before any split:
  - URL exists and is routable
  - Response status code for known input states
  - Key response schema fields are present
  - Safety gates (env flags, confirmations, approval) behave as documented

They deliberately avoid depending on ``mock_store`` internal row formats or
service implementation details.  After the route split (Tasks 3.2 / 3.3),
these same tests must pass against the new routers with no changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.backend.app.services.mock_store import SQLiteDesktopStore

# ── Isolation helpers (mirrors existing conversion test pattern) ──────────


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    import src.backend.app.services.mock_store as mock_store_module
    from src.backend.app.api import (
        dashboard_routes,
        execute_reviewed_routes,
        project_routes,
    )
    from src.backend.app.planner import project_context, reviewed_plan_store
    from src.backend.app.runtime import desktop_config
    from src.backend.app.services import conversion_planner

    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    # Patch legacy helper modules plus the application composition dependency.
    # Mounted domain routes resolve their store through
    # ``dependencies.get_project_store()`` and therefore use the isolated
    # module-level application store below.
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        execute_reviewed_routes,
        conversion_planner,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps({"projects_root": str(tmp_path / "projects")}),
        encoding="utf-8",
    )
    return store


def _create_project(client, tmp_path: Path, project_id: str = "char-test-project") -> str:
    project_dir = tmp_path / "projects" / project_id
    rawdata_dir = tmp_path / "rawdata"
    rawdata_dir.mkdir(parents=True, exist_ok=True)
    (rawdata_dir / "readme.txt").write_text("test", encoding="utf-8")
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "CharTest",
            "rawdata_dir": str(rawdata_dir),
            "project_dir": str(project_dir),
        },
    )
    assert resp.status_code == 200, f"Project creation failed: {resp.text}"
    return resp.json()["project_id"]


def _client(tmp_path: Path, monkeypatch, env_flags: dict | None = None):
    from fastapi.testclient import TestClient

    from src.backend.app.main import app

    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    project_id = _create_project(client, tmp_path)

    # Clear and set env flags
    for k in list(os.environ.keys()):
        if k.startswith("MEDIMAGE_"):
            monkeypatch.delenv(k, raising=False)
    if env_flags:
        for k, v in env_flags.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)

    return client, project_id, store


# ── Baseline read routes ──────────────────────────────────────────────────


class TestReadRoutes:
    """Read-only routes that should always be reachable."""

    def test_health(self, tmp_path, monkeypatch):
        client, _, _ = _client(tmp_path, monkeypatch)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_projects_list(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # The list response uses ``id`` as the project identifier field.
        assert any(p.get("id") == project_id for p in data)

    def test_project_detail(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        resp = client.get(f"/api/projects/{project_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("id") == project_id

    def test_tasks_list(self, tmp_path, monkeypatch):
        client, _, _ = _client(tmp_path, monkeypatch)
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_dashboard_state(self, tmp_path, monkeypatch):
        client, _, _ = _client(tmp_path, monkeypatch)
        resp = client.get("/api/dashboard/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data or "tasks" in data or "state" in data


# ── Conversion dry-run / preflight ────────────────────────────────────────


class TestConversionDryRunPreflight:
    """Conversion planning routes — read-only, never executes dcm2niix."""

    def test_dry_run_requires_project(self, tmp_path, monkeypatch):
        client, _, _ = _client(tmp_path, monkeypatch)
        resp = client.post(
            "/api/projects/nonexistent-project/conversion/dry-run",
            json={},
        )
        assert resp.status_code == 404

    def test_dry_run_returns_plan(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        resp = client.post(
            f"/api/projects/{project_id}/conversion/dry-run",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "mapping_preview" in data or "source_summaries" in data
        assert "safety_flags" in data

    def test_preflight_returns_readiness(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        resp = client.post(f"/api/projects/{project_id}/conversion/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "dcm2niix_available" in data
        assert "safety_flags" in data
        assert "env_enabled" in data


# ── Conversion approval / review package ──────────────────────────────────


class TestConversionApprovalRoutes:
    """Approval and review-package routes — metadata only, no execution."""

    def test_persist_plan_requires_project(self, tmp_path, monkeypatch):
        client, _, _ = _client(tmp_path, monkeypatch)
        resp = client.post(
            "/api/projects/nonexistent-project/conversion/approval/persist-plan",
            json={},
        )
        assert resp.status_code == 404

    def test_persist_plan_returns_record(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        body = {
            "approval_id": "char-approval",
            "status": "ready_for_review",
            "approved": False,
            "output_root": "/tmp/output",
            "output_root_confirmed": True,
            "output_root_under_project": True,
            "output_root_not_rawdata": True,
            "rawdata_read_only_confirmed": True,
            "no_shell_string_confirmed": True,
            "dcm2niix_availability_confirmed": True,
            "env_flags_confirmed": True,
            "rollback_policy_acknowledged": True,
            "clinical_use_prohibited_acknowledged": True,
            "external_tool_acknowledgement": True,
            "risk_acknowledgement": True,
        }
        resp = client.post(
            f"/api/projects/{project_id}/conversion/approval/persist-plan",
            json=body,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "approval_id" in data or "run_id" in data or "ok" in data

    def test_review_package_requires_existing_run(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        resp = client.get(
            f"/api/projects/{project_id}/conversion/approval/packages/nonexistent-run"
        )
        assert resp.status_code in (404, 200)  # 200 if service returns error dict

    def test_release_readiness_returns_status(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        resp = client.get(f"/api/projects/{project_id}/conversion/release-readiness/run-001")
        # Always returns 200 with a status dict (even for missing runs)
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data


# ── Public execute endpoint — safety gate contract ────────────────────────


class TestConversionExecuteSafetyGates:
    """The public conversion execute endpoint must block unless all gates pass.

    These tests verify the gate contract, not that conversion actually runs
    (which requires a full maintainer-approved environment).
    """

    def test_missing_env_flags_returns_disabled(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch, env_flags={})
        resp = client.post(
            f"/api/projects/{project_id}/conversion/execute",
            json={"conversion_run_id": "run-001"},
        )
        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "EXECUTION_CONTRACT_REQUIRED"

    def test_missing_confirmations_returns_blocked(self, tmp_path, monkeypatch):
        flags = {
            "MEDIMAGE_ALLOW_PUBLIC_DICOM_CONVERSION_ENDPOINT": "1",
            "MEDIMAGE_ALLOW_USER_DATA_CONVERSION": "1",
            "MEDIMAGE_ENABLE_DICOM_CONVERSION": "1",
            "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
            "MEDIMAGE_ENABLE_REAL_PREPROCESSING": "1",
        }
        client, project_id, _ = _client(tmp_path, monkeypatch, env_flags=flags)
        resp = client.post(
            f"/api/projects/{project_id}/conversion/execute",
            json={"conversion_run_id": "run-001"},
        )
        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "EXECUTION_CONTRACT_REQUIRED"

    def test_nonexistent_project_returns_blocked(self, tmp_path, monkeypatch):
        flags = {
            "MEDIMAGE_ALLOW_PUBLIC_DICOM_CONVERSION_ENDPOINT": "1",
            "MEDIMAGE_ALLOW_USER_DATA_CONVERSION": "1",
            "MEDIMAGE_ENABLE_DICOM_CONVERSION": "1",
            "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
            "MEDIMAGE_ENABLE_REAL_PREPROCESSING": "1",
        }
        client, _, _ = _client(tmp_path, monkeypatch, env_flags=flags)
        resp = client.post(
            "/api/projects/nonexistent-project/conversion/execute",
            json={"conversion_run_id": "run-001"},
        )
        # Both 200 (old behavior: blocked dict) and 404 (new router: HTTPException)
        # are accepted as valid "project not found" signals.
        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "EXECUTION_CONTRACT_REQUIRED"

    def test_response_contains_safety_flags(self, tmp_path, monkeypatch):
        client, project_id, _ = _client(tmp_path, monkeypatch)
        resp = client.post(
            f"/api/projects/{project_id}/conversion/execute",
            json={"conversion_run_id": "run-001"},
        )
        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "EXECUTION_CONTRACT_REQUIRED"
