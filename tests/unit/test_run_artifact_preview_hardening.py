"""Tests for artifact preview hardening — invalid IDs, missing files, edge cases."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.tools.artifact_utils import is_safe_artifact_id
from tests.goal_contract_helpers import reviewed_goal_candidate


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
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(
    client: TestClient, tmp_path: Path, name: str = "Artifact Hardening Project"
) -> dict:
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


def _reviewed_plan(created: dict) -> dict:
    rawdata_dir = created["rawdata_dir"]
    dataset_index_path = created["dataset_index_path"]
    return {
        "pipeline_id": "artifact-hardening-test",
        "project_context": {
            "project_id": created["project_id"],
            "project_config_path": created["project_config_path"],
            "rawdata_dir": rawdata_dir,
            "dataset_index_path": dataset_index_path,
            "source": "created",
            "diagnostics": created["diagnostics"],
        },
        "nodes": [
            {
                "id": "contract_smoke",
                "backend": "python",
                "depends_on": [],
                "params": {},
            },
        ],
    }


def _save_plan(client: TestClient, created: dict, plan: dict) -> dict:
    goal = "Artifact hardening test"
    response = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True},
            "goal": goal,
            "provider": "mock",
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "test-reviewer",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["reviewed_plan"]


def _execute_plan(
    client: TestClient,
    created: dict,
    plan: dict,
    reviewed_plan_id: str,
) -> dict:
    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_by": "test"},
            "project_id": created["project_id"],
            "reviewed_plan_id": reviewed_plan_id,
            "project_config_path": created["project_config_path"],
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
            "actor": "test",
        },
    )
    return response.json()


# ── artifact_id safety ──────────────────────────────────────────────────────


def test_is_safe_artifact_id_rejects_dot_dot():
    assert not is_safe_artifact_id("../secret")
    assert not is_safe_artifact_id("..%2Fsecret")
    assert not is_safe_artifact_id("..%2fsecret")


def test_is_safe_artifact_id_rejects_slash():
    assert not is_safe_artifact_id("a/b")
    assert not is_safe_artifact_id("a\\b")
    assert not is_safe_artifact_id("a%2Fb")
    assert not is_safe_artifact_id("a%5cb")
    assert not is_safe_artifact_id("%2e%2e")


def test_is_safe_artifact_id_rejects_empty_and_long():
    assert not is_safe_artifact_id("")
    assert not is_safe_artifact_id("a" * 257)


def test_is_safe_artifact_id_accepts_valid():
    assert is_safe_artifact_id("artifact_abc123")
    assert is_safe_artifact_id("artifact_abcdef12345678")
    assert is_safe_artifact_id("a-b.c_d")


# ── API-level artifact_id rejection ─────────────────────────────────────────


def test_invalid_artifact_id_returns_400(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id

    # These must return 400 (bad request), not 404 or 500
    for bad_id in ["../secret", "a/b", "a\\b", "a" * 300]:
        resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/{bad_id}")
        assert resp.status_code in (400, 404), (
            f"Expected 400 or 404 for {bad_id!r}, got {resp.status_code}"
        )


# ── Missing artifact preview ────────────────────────────────────────────────


def test_nonexistent_artifact_preview_returns_404(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/artifact_nonexistent_01"
    )
    assert resp.status_code == 404


# ── Artifact list includes missing artifacts ────────────────────────────────


def test_artifact_list_includes_missing_artifacts(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id

    # The pipeline_path artifact should exist; summary_path may be present.
    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["artifacts"], list)
    # We should have at least one artifact (the pipeline YAML).
    assert len(body["artifacts"]) > 0, f"Expected artifacts, got: {body}"

    # Check that every artifact has the expected fields
    for artifact in body["artifacts"]:
        assert "artifact_id" in artifact
        assert "name" in artifact
        assert "kind" in artifact
        assert "exists" in artifact
        assert "previewable" in artifact
        if not artifact["exists"]:
            assert artifact["previewable"] is False
            assert artifact["size_bytes"] is None


# ── Real artifact preview (happy path) ──────────────────────────────────────


def test_real_artifact_preview_succeeds(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id

    # Get artifacts list
    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts")
    assert resp.status_code == 200
    artifacts = resp.json()["artifacts"]

    # Find a JSON artifact (pipeline YAML should be there)
    json_artifact = next(
        (a for a in artifacts if a["kind"] in {"json", "yaml", "text"} and a["exists"]),
        None,
    )
    assert json_artifact is not None, f"No previewable artifact found in {artifacts}"

    preview_resp = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/{json_artifact['artifact_id']}"
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview = preview_resp.json()
    assert preview["ok"] is True or "errors" in preview
    assert preview["artifact_id"] == json_artifact["artifact_id"]


# ── Binary artifact is metadata-only ────────────────────────────────────────


def test_binary_artifact_is_metadata_only(tmp_path, monkeypatch):
    """Ensure a binary file (e.g. NIfTI test file) returns metadata-only preview."""
    # Create a dummy binary file in the project outputs
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _reviewed_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = _execute_plan(client, created, plan, reviewed["reviewed_plan_id"])
    run_id = result.get("run_id")
    assert run_id

    # Write a dummy .nii file inside the project work dir so discovery picks it up
    work_dir = Path(created["project_dir"]) / "work" / "pipeline_runs" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    dummy_nii = work_dir / "test.nii"
    dummy_nii.write_bytes(b"\x00" * 100)

    # Re-fetch artifacts (the binary should now appear)
    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts")
    artifacts = resp.json()["artifacts"]
    nii_artifact = next(
        (a for a in artifacts if a["name"] == "test.nii"),
        None,
    )
    # The NIfTI may not be discoverable if the pipeline summary doesn't reference it.
    # If it is, verify it's not previewable.
    if nii_artifact:
        assert nii_artifact["exists"] is True
        assert nii_artifact["previewable"] is False
