"""Tests for POST /api/projects/{project_id}/spm-realign/wrapper-skeleton."""

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
from src.backend.app.services import (
    bold_reference_readiness,
    motion_qc_readiness,
    spm_realign_dry_run,
    spm_realign_wrapper_skeleton,
)
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
        spm_realign_dry_run,
        spm_realign_wrapper_skeleton,
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
            "project_name": "Skeleton Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "skeleton_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.post("/api/projects/nonexistent/spm-realign/wrapper-skeleton")
    assert resp.status_code == 404


def test_returns_structured_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True or body["status"] == "blocked"
    assert body["command_template_id"] == "spm12_realign_estwrite_v1"
    assert "matlab_batch_preview" in body


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    flags = resp.json()["safety_flags"]
    for key in (
        "preview_only",
        "no_matlab_called",
        "no_spm_called",
        "no_files_created",
        "rawdata_not_modified",
        "not_safe_allowlisted",
        "execution_disabled",
    ):
        assert flags.get(key) is True


def test_matlab_batch_contains_preview_only(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    batch = resp.json()["matlab_batch_preview"]
    if batch:
        assert "PREVIEW ONLY" in batch
        assert "matlab -batch" not in batch.lower()


def test_provenance_fields(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    pp = resp.json()["provenance_preview"]
    assert pp["execution_enabled"] is False
    assert pp["safe_allowlist_enabled"] is False
    assert pp["approval_required"] is True
    assert pp["audit_required"] is True


def test_no_files_created(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    project_dir = Path(created["project_dir"])
    before = {str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()
    client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    after = {str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()
    assert after == before


# ── Manifest response regression tests ─────────────────────────────────────


def test_wrapper_response_includes_output_manifests(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    body = resp.json()
    assert "output_manifests" in body
    manifests = body["output_manifests"]
    assert isinstance(manifests, list)
    for m in manifests:
        assert "project_id" in m
        assert "run_id" in m
        assert m.get("node_id") == "spm_realign_subject"
        assert "output_root" in m
        assert "items" in m
        assert "missing_required_count" in m
        assert "verified_count" in m


def test_wrapper_response_manifest_items_have_expected_kinds(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    body = resp.json()
    manifests = body["output_manifests"]
    # If the fixture has no BOLD inputs, manifests list may be empty — that is valid
    if manifests:
        all_kinds = set()
        for m in manifests:
            for item in m.get("items", []):
                all_kinds.add(item["kind"])
        expected = {
            "realigned_bold",
            "mean_bold",
            "motion_params",
            "stdout_log",
            "stderr_log",
            "provenance_json",
            "node_state_json",
        }
        missing = expected - all_kinds
        assert len(missing) == 0, f"Missing output kinds in manifest: {missing}"


def test_wrapper_response_manifest_summary_counts_are_stable(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    body = resp.json()
    summary = body["manifest_summary"]
    assert isinstance(summary, dict)
    for key in (
        "manifest_count",
        "total_items",
        "missing_required_count",
        "verified_count",
        "would_overwrite_count",
    ):
        assert key in summary, f"manifest_summary missing key: {key}"
        assert isinstance(summary[key], int), f"{key} should be int, got {type(summary[key])}"
    assert summary["verified_count"] == 0
    assert summary["missing_required_count"] >= 0
    assert summary["total_items"] >= 0


def test_wrapper_preview_manifests_are_not_verified(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    body = resp.json()
    for m in body["output_manifests"]:
        for item in m.get("items", []):
            assert item["verified"] is False, (
                f"Item {item['kind']} should not be verified in preview"
            )


def test_wrapper_manifest_integration_creates_no_files(tmp_path, monkeypatch):
    """Wrapper skeleton generation + manifest building must not create files."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    _project_dir = Path(created["project_dir"])
    # Include the entire project tree, not just the project_dir
    before_all = {str(p) for p in Path(tmp_path).rglob("*")} if tmp_path.exists() else set()
    client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    after_all = {str(p) for p in Path(tmp_path).rglob("*")} if tmp_path.exists() else set()
    new_files = after_all - before_all
    assert not new_files, f"Wrapper skeleton created unexpected files: {new_files}"
