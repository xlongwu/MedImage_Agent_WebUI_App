"""Tests for POST /api/projects/{project_id}/conversion/dry-run."""

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
from src.backend.app.services import conversion_planner
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
        conversion_planner,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(client: TestClient, tmp_path: Path, name: str = "Conversion Project") -> dict:
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


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.post("/api/projects/nonexistent/conversion/dry-run", json={})
    assert resp.status_code == 404


def test_dry_run_returns_structured_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["dry_run"] is True
    assert body["status"] in ("ready", "warning", "blocked", "unknown")
    assert isinstance(body["source_summaries"], list)
    assert isinstance(body["mapping_preview"], list)
    assert isinstance(body["safety_flags"], dict)


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={},
    )
    flags = resp.json()["safety_flags"]
    assert flags.get("dry_run_only") is True
    assert flags.get("rawdata_read_only") is True
    assert flags.get("no_files_written") is True
    assert flags.get("no_external_tools_executed") is True
    assert flags.get("requires_user_review_before_conversion") is True
    assert flags.get("output_path_is_preview_only") is True


def test_dry_run_does_not_create_files(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    project_dir = Path(created["project_dir"])

    # Snapshot before
    before = set()
    if project_dir.exists():
        before = {str(p.relative_to(project_dir)) for p in project_dir.rglob("*")}

    client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={},
    )

    # Snapshot after — nothing new should exist
    after = set()
    if project_dir.exists():
        after = {str(p.relative_to(project_dir)) for p in project_dir.rglob("*")}
    new_files = after - before
    assert not new_files, f"Dry-run created files: {new_files}"


def test_request_cannot_inject_arbitrary_source_path(tmp_path, monkeypatch):
    """The endpoint ignores arbitrary path fields — only project-scoped roots are used."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"source_import_ids": [], "output_root_name": "../../etc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # output_root_preview should be project-scoped, not "../../etc"
    preview = body.get("output_root_preview") or ""
    assert "../" not in preview


def test_synthetic_bids_classified_as_bids(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"include_loose_nifti": True},
    )
    body = resp.json()
    types = {s["source_type"] for s in body["source_summaries"]}
    # The synthetic BIDS fixture should be classified as "bids"
    assert "bids" in types, f"Expected 'bids' in source types, got: {types}"


def test_blocking_status_when_no_convertible(tmp_path, monkeypatch):
    """When include_dicom=False and include_loose_nifti=False, no mappings produced."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"include_dicom": False, "include_loose_nifti": False},
    )
    body = resp.json()
    assert body["status"] == "blocked"
    assert len(body["blocking_issues"]) > 0


def test_mapping_confidence_fields_present(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={},
    )
    body = resp.json()
    for mapping in body["mapping_preview"]:
        assert "confidence" in mapping
        assert mapping["confidence"] in ("high", "medium", "low", "manual_required")
        assert "suggested_relative_path" in mapping


def test_latest_dry_run_restores_persisted_mapping_snapshot(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    project_dir = Path(created["project_dir"])
    run_dir = project_dir / "conversion_runs" / "conv-restored"
    run_dir.mkdir(parents=True)
    (run_dir / "mapping_snapshot.json").write_text(
        json.dumps(
            {
                "created_at": "2026-06-25T00:00:00Z",
                "mappings": [
                    {
                        "source_path": str(project_dir / "rawdata" / "sub-01" / "REST"),
                        "source_type": "dicom_series",
                        "subject_id": "sub-01",
                        "session_id": "ses-01",
                        "modality": "func",
                        "suffix": "bold",
                        "task": "rest",
                        "suggested_relative_path": "sub-01/ses-01/func/sub-01_task-rest_bold.nii.gz",
                        "confidence": "high",
                        "warnings": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "preflight_snapshot.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "checked_at": "2026-06-25T00:00:00Z",
                "output_root_name": "converted_bids",
                "source_summaries": [
                    {
                        "source_id": "source-1",
                        "source_type": "dicom",
                        "root": str(project_dir / "rawdata"),
                        "exists": True,
                        "file_count": 12,
                        "subject_candidates": ["sub-01"],
                        "series_count": 1,
                        "warnings": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    resp = client.get(f"/api/projects/{created['project_id']}/conversion/dry-run/latest")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["mapping_preview"][0]["source_type"] == "dicom_series"
    assert body["mapping_preview"][0]["subject_id"] == "sub-01"
    assert body["safety_flags"]["dry_run_only"] is True
    assert body["safety_flags"]["no_external_tools_executed"] is True
    assert body["safety_flags"]["restored_from_persisted_review_package"] is True
    assert any("Restored dry-run mappings" in warning for warning in body["warnings"])


def test_output_root_preview_is_scoped(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"output_root_name": "test_converted"},
    )
    body = resp.json()
    assert body["output_root_name"] == "test_converted"
    # output_root_preview should be a project-relative path (may be None
    # if the store doesn't have project_dir metadata in the test fixture)
    preview = body.get("output_root_preview")
    if preview is not None:
        assert "test_converted" in preview


def test_plan_conversion_supports_dict_shaped_input(tmp_path, monkeypatch):
    from src.backend.app.services.conversion_planner import plan_conversion

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    # Call plan_conversion directly with a raw dict request
    raw_dict = {
        "output_root_name": "custom_root_dict",
        "include_dicom": True,
        "source_import_ids": [],
    }
    res = plan_conversion(created["project_id"], raw_dict)
    assert res.ok is True
    assert res.output_root_name == "custom_root_dict"
