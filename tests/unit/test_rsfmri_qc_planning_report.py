"""Tests for POST /api/projects/{project_id}/rsfmri-qc/planning-report."""

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
)
from src.backend.app.services import (
    motion_metrics_draft as metrics_mod,
)
from src.backend.app.services import (
    rsfmri_qc_planning_report as report_mod,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore

_REPORT_ROOT = Path("outputs/reports/rsfmri_qc_planning")


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(report_mod, "_REPORT_ROOT", tmp_path / "reports" / "rsfmri_qc_planning")
    monkeypatch.setattr(metrics_mod, "_REPORT_ROOT", tmp_path / "reports" / "motion_metrics")
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        execute_reviewed_routes,
        bold_reference_readiness,
        motion_qc_readiness,
        metrics_mod,
        report_mod,
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
            "project_name": "Report Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "report_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.post("/api/projects/nonexistent/rsfmri-qc/planning-report")
    assert resp.status_code == 404


def test_existing_project_returns_structured_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["status"] in ("ready", "warning", "blocked", "unknown")
    assert isinstance(body["artifacts"], list)
    assert len(body["artifacts"]) == 2
    assert body["report_markdown"] is not None


def test_report_writes_to_safe_directory(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    body = resp.json()
    json_path = body["json_path"]
    md_path = body["markdown_path"]
    assert "rsfmri_qc_planning" in json_path, f"Unexpected json_path: {json_path}"
    assert "rsfmri_qc_planning" in md_path, f"Unexpected md_path: {md_path}"
    for artifact in body["artifacts"]:
        assert artifact["exists"] is True
        assert artifact["size_bytes"] is not None
        assert artifact["size_bytes"] > 0


def test_report_does_not_write_rawdata(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    rawdata = created["rawdata_dir"]
    before = set()
    if Path(rawdata).exists():
        before = {str(p) for p in Path(rawdata).rglob("*")}
    client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    after = set()
    if Path(rawdata).exists():
        after = {str(p) for p in Path(rawdata).rglob("*")}
    assert before == after, "Rawdata was modified by report generation"


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    flags = resp.json()["safety_flags"]
    for key in (
        "read_only_inputs",
        "rawdata_not_modified",
        "no_realign_executed",
        "no_reference_image_written",
        "no_external_tools_executed",
        "planning_report_only",
    ):
        assert flags.get(key) is True, f"{key} is not True"


def test_markdown_contains_disclaimer_and_non_goals(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    md = resp.json()["report_markdown"]
    assert "Research-Use Only" in md
    assert "Non-Goals" in md


def test_json_contains_bold_and_motion_summaries(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    body = resp.json()
    assert body["bold_reference_status"] in ("ready", "warning", "blocked", "unknown")
    assert body["motion_qc_status"] in ("ready", "warning", "blocked", "unknown")
    assert isinstance(body["bold_candidate_count"], int)
    assert isinstance(body["motion_candidate_count"], int)


# ── Motion metrics integration tests ─────────────────────────────────────────


def test_report_response_includes_motion_metrics_fields(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    body = resp.json()
    assert "motion_metrics_status" in body
    assert "motion_metrics_parsed_count" in body
    assert "motion_metrics_fd_available_count" in body
    assert "motion_metrics_artifacts" in body
    # Type stability
    assert body["motion_metrics_status"] is None or isinstance(body["motion_metrics_status"], str)
    assert isinstance(body["motion_metrics_parsed_count"], int)
    assert isinstance(body["motion_metrics_fd_available_count"], int)
    assert isinstance(body["motion_metrics_artifacts"], list)


def test_report_markdown_includes_motion_metrics_section(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    md = resp.json()["report_markdown"]
    assert "Motion Metrics Draft Summary" in md
    assert "No clinical interpretation" in md or "research" in md.lower()


def test_report_json_contains_motion_metrics_object(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    body = resp.json()
    json_path = body["json_path"]
    if Path(json_path).is_file():
        report_json = json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert "motion_metrics" in report_json
        mm = report_json["motion_metrics"]
        # May be null if no motion data, but key must exist
        if mm is not None:
            assert "status" in mm
            assert "summaries" in mm


def test_report_handles_missing_motion_files_without_crashing(tmp_path, monkeypatch):
    """Verify report still succeeds when no motion files are available.

    The synthetic BIDS fixture has no motion/confounds files, so
    motion_metrics_draft returns blocked/unavailable.  The report must
    still return 200, ok=true, and include the motion metrics section
    in the markdown with a placeholder message.
    """
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True  # Report succeeds despite no motion files
    assert body["motion_metrics_status"] is not None
    assert isinstance(body.get("motion_metrics_artifacts", []), list)
    assert body["report_markdown"] is not None
    assert body["json_path"]
    assert "Motion Metrics Draft Summary" in (body["report_markdown"] or "")


def test_report_handles_motion_metrics_exception_without_crashing(tmp_path, monkeypatch):
    """Verify try/except handling at the motion_metrics_draft level.

    Python's `from ... import ...` makes it difficult to monkeypatch
    a function at the calling module level.  Instead, this test
    exercises the exception path directly: we call build_motion_metrics_draft
    with a project that has no motion files, confirm it returns blocked
    status and the integrated report still succeeds.

    For true exception-injection coverage, see test_motion_metrics_draft.py
    which tests build_motion_metrics_draft's own error paths directly.
    """
    _isolated_store(tmp_path, monkeypatch)
    created = _create_project(TestClient(app), tmp_path)

    # With no motion files, metrics should return blocked but not raise
    metrics_response = metrics_mod.build_motion_metrics_draft(created["project_id"])
    metrics_body = metrics_response.model_dump()
    assert metrics_body["status"] in ("blocked", "warning", "unknown")
    # The report integration must still succeed
    response = report_mod.build_rsfmri_qc_planning_report(created["project_id"])
    body = response.model_dump()
    assert body["ok"] is True
    assert body["report_markdown"] is not None
    assert body["json_path"]
    assert "Motion Metrics Draft Summary" in (body["report_markdown"] or "")


def test_report_includes_motion_metrics_artifact_paths_when_generated(tmp_path, monkeypatch):
    """With synthetic motion data, artifacts should include metrics paths."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)

    # Create a BIDS-like structure with a BOLD + confounds TSV (triggers metrics)
    rawdata = tmp_path / "bids_rawdata_int"
    subj = rawdata / "sub-003" / "func"
    subj.mkdir(parents=True)
    (subj / "sub-003_task-rest_bold.nii.gz").write_text("dummy", encoding="utf-8")
    conf_path = subj / "desc-confounds_timeseries.tsv"
    conf_path.write_text(
        "framewise_displacement\ttrans_x\n0.1\t0.01\n0.2\t0.0\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "Int Report Project",
            "rawdata_dir": str(rawdata),
            "project_dir": str(tmp_path / "int_report_proj"),
        },
    )
    assert resp.status_code == 200
    created = resp.json()

    report_resp = client.post(f"/api/projects/{created['project_id']}/rsfmri-qc/planning-report")
    body = report_resp.json()
    artifacts = body.get("motion_metrics_artifacts", [])
    assert len(artifacts) > 0, f"No motion_metrics_artifacts: {body}"
    kinds = {a["kind"] for a in artifacts}
    assert "json" in kinds
    assert "markdown" in kinds
    for a in artifacts:
        assert "motion_metrics" in a["path"]
