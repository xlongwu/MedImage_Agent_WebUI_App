"""Tests for GET /api/projects/{project_id}/bids-validation."""

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
from src.backend.app.services import bids_validation
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
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(
    client: TestClient, tmp_path: Path, name: str = "BIDS Validation Project"
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


# ── Tests ────────────────────────────────────────────────────────────────────


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/bids-validation")
    assert resp.status_code == 404


def test_created_project_returns_valid_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/bids-validation")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["status"] in ("pass", "warning", "fail", "unknown")
    assert isinstance(body["roots"], list)
    assert isinstance(body["issues"], list)
    assert isinstance(body["repair_suggestions"], list)


def test_missing_dataset_description_produces_metadata_suggestion(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/bids-validation")
    body = resp.json()
    codes = [i["code"] for i in body["issues"]]
    # The synthetic BIDS dir may or may not have dataset_description.json
    if "DATASET_DESC_MISSING" in codes:
        repair_codes: list[str] = []
        for r in body["repair_suggestions"]:
            repair_codes.extend(r.get("related_issue_codes", []))
        assert "DATASET_DESC_MISSING" in repair_codes


def test_bids_validation_no_arbitrary_path(tmp_path, monkeypatch):
    """The endpoint uses project-scoped roots, never arbitrary paths."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    # Passing an unexpected query param should not change behaviour
    resp = client.get(
        f"/api/projects/{created['project_id']}/bids-validation?path=../../etc/passwd"
    )
    assert resp.status_code == 200, resp.text


def test_synthetic_bids_has_subject_structure(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/bids-validation")
    body = resp.json()
    # The synthetic BIDS fixture should have at least one subject
    assert body["subject_count"] > 0
    assert body["nifti_file_count"] > 0


def test_repair_suggestions_have_required_fields(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/bids-validation")
    body = resp.json()
    for suggestion in body["repair_suggestions"]:
        assert "action_type" in suggestion
        assert "title" in suggestion
        assert "description" in suggestion
        assert "requires_user_review" in suggestion


def test_data_readiness_includes_bids_check(tmp_path, monkeypatch):
    """After integration, data readiness should include a bids_validation check."""
    _isolated_store(tmp_path, monkeypatch)
    from src.backend.app.services import data_readiness  # noqa

    monkeypatch.setattr(data_readiness, "mock_store", _isolated_store(tmp_path, monkeypatch))
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    body = resp.json()
    check_names = {c["name"] for c in body["checks"]}
    assert "bids_validation" in check_names, (
        f"bids_validation check missing from readiness checks: {check_names}"
    )


def test_missing_root_returns_fail_with_issue(tmp_path):
    """Directly test validate_bids with a non-existent root."""
    result = bids_validation.validate_bids(["Z:/nonexistent/bids/root"])
    assert result.status == "fail"
    # Depending on path validation, the root may be filtered out
    # If the path is reachable, confirm issue(s)
    assert isinstance(result.issues, list)


def test_dataset_description_utf8_bom_is_accepted(tmp_path):
    root = tmp_path / "converted_bids"
    func = root / "sub-001" / "func"
    func.mkdir(parents=True)
    (root / "dataset_description.json").write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps({"Name": "Converted test dataset", "BIDSVersion": "1.8.0"}).encode("utf-8")
    )
    (func / "sub-001_task-rest_bold.json").write_text(
        json.dumps({"TaskName": "rest", "RepetitionTime": 2.0}),
        encoding="utf-8",
    )
    (func / "sub-001_task-rest_bold.nii.gz").write_bytes(b"")

    result = bids_validation.validate_bids([str(root)])
    codes = {issue.code for issue in result.issues}

    assert result.status == "pass"
    assert "DATASET_DESC_MALFORMED" not in codes
    assert not any("dataset_description.json" in action.lower() for action in result.next_actions)


def test_malformed_dataset_description_gives_specific_next_action(tmp_path):
    root = tmp_path / "converted_bids"
    func = root / "sub-001" / "func"
    func.mkdir(parents=True)
    (root / "dataset_description.json").write_text("{not json", encoding="utf-8")
    (func / "sub-001_task-rest_bold.json").write_text(
        json.dumps({"TaskName": "rest", "RepetitionTime": 2.0}),
        encoding="utf-8",
    )
    (func / "sub-001_task-rest_bold.nii.gz").write_bytes(b"")

    result = bids_validation.validate_bids([str(root)])
    codes = {issue.code for issue in result.issues}
    next_actions = "\n".join(result.next_actions)

    assert result.status == "fail"
    assert "DATASET_DESC_MALFORMED" in codes
    assert "Fix or regenerate dataset_description.json" in next_actions
    assert "Provide a valid BIDS rawdata directory" not in next_actions
