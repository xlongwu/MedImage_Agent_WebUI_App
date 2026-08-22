"""Tests for POST /api/projects/{project_id}/motion-qc/metrics-draft."""

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
    qc_evidence_roots,
)
from src.backend.app.services import (
    motion_metrics_draft as metrics_mod,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(metrics_mod, "_REPORT_ROOT", tmp_path / "reports" / "motion_metrics")
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        execute_reviewed_routes,
        bold_reference_readiness,
        motion_qc_readiness,
        qc_evidence_roots,
        metrics_mod,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _create_project(client: TestClient, tmp_path: Path, rawdata: Path | None = None) -> dict:
    rd = str(rawdata or Path("examples/synthetic_bids/rawdata").resolve())
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "Metrics Project",
            "rawdata_dir": rd,
            "project_dir": str(tmp_path / "metrics_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.post("/api/projects/nonexistent/motion-qc/metrics-draft")
    assert resp.status_code == 404


def test_existing_project_returns_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] in ("ready", "warning", "blocked", "unknown")


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    flags = resp.json()["safety_flags"]
    for key in (
        "read_only_inputs",
        "rawdata_not_modified",
        "no_realign_executed",
        "no_external_tools_executed",
        "qc_summary_only",
        "no_clinical_interpretation",
    ):
        assert flags.get(key) is True


def test_markdown_contains_disclaimer(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    md = resp.json().get("report_markdown") or ""
    assert "Research-Use Only" in md
    assert "Non-Goals" in md


def test_report_writes_to_safe_directory(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    body = resp.json()
    assert "motion_metrics" in body["json_path"]
    for a in body["artifacts"]:
        assert a["exists"] is True
        assert a["size_bytes"] > 0


def test_spm_rp_txt_parsed(tmp_path, monkeypatch):
    """Write a synthetic rp_*.txt and verify metrics are computed."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)

    # Create a BIDS-like structure with a BOLD file + rp_*.txt
    rawdata = tmp_path / "bids_rawdata"
    subj = rawdata / "sub-001" / "func"
    subj.mkdir(parents=True)
    (subj / "sub-001_task-rest_bold.nii.gz").write_text("dummy", encoding="utf-8")
    rp_path = subj / "rp_sub-001_task-rest_bold.txt"
    rp_path.write_text(
        "0.1 0.2 0.3 0.01 0.02 0.03\n-0.1 -0.2 -0.3 -0.01 -0.02 -0.03\n0.0 0.0 0.0 0.0 0.0 0.0\n",
        encoding="utf-8",
    )

    created = _create_project(client, tmp_path, rawdata)
    resp = client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    body = resp.json()
    parsed = [s for s in body["summaries"] if s.get("parsed")]
    assert len(parsed) > 0, f"No parsed summaries: {body['summaries']}"
    assert parsed[0]["source_type"] == "spm_rp_txt"
    assert parsed[0]["row_count"] == 3


def test_confounds_tsv_fd_parsed(tmp_path, monkeypatch):
    """Write a synth confounds TSV with framewise_displacement and verify."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)

    rawdata = tmp_path / "bids_rawdata2"
    subj = rawdata / "sub-002" / "func"
    subj.mkdir(parents=True)
    (subj / "sub-002_task-rest_bold.nii.gz").write_text("dummy", encoding="utf-8")
    conf_path = subj / "desc-confounds_timeseries.tsv"
    conf_path.write_text(
        "framewise_displacement\ttrans_x\ttrans_y\n"
        "0.1\t0.01\t0.02\n"
        "0.3\t-0.01\t0.0\n"
        "0.6\t0.0\t0.0\n",
        encoding="utf-8",
    )

    created = _create_project(client, tmp_path, rawdata)
    resp = client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    body = resp.json()
    parsed = [s for s in body["summaries"] if s.get("parsed")]
    assert len(parsed) > 0
    fd_entry = [s for s in parsed if s.get("has_fd")]
    assert len(fd_entry) > 0, f"No FD entry: {parsed}"
    assert fd_entry[0]["fd_mean"] is not None
    assert fd_entry[0]["fd_over_0_2_count"] == 2
    assert fd_entry[0]["fd_over_0_5_count"] == 1


def test_native_fd_source_ignores_auxiliary_empty_tsvs(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)

    rawdata = tmp_path / "bids_native_motion"
    func = rawdata / "sub-001" / "func"
    func.mkdir(parents=True)
    (func / "sub-001_task-rest_bold.nii.gz").write_text("dummy", encoding="utf-8")
    created = _create_project(client, tmp_path, rawdata)

    motion_dir = (
        tmp_path
        / "metrics_proj"
        / "preprocessing_native_runs"
        / "pp-test"
        / "artifacts"
        / "motion_qc"
    )
    motion_dir.mkdir(parents=True)
    fd_path = (
        motion_dir / "slice_timing_bold_desc-motion_parameters_desc-framewise_displacement.tsv"
    )
    fd_path.write_text(
        "framewise_displacement\n0.00000000\n0.10000000\n0.30000000\n",
        encoding="utf-8",
    )
    (
        motion_dir / "slice_timing_bold_desc-motion_parameters_desc-friston24_regressors.tsv"
    ).write_text(
        "trans_x\ttrans_y\n",
        encoding="utf-8",
    )
    (motion_dir / "slice_timing_bold_desc-motion_parameters.tsv").write_text(
        "trans_x\ttrans_y\ttrans_z\trot_x\trot_y\trot_z\n",
        encoding="utf-8",
    )

    resp = client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["parsed_count"] == 1
    assert body["fd_available_count"] == 1
    assert "No valid data rows found" not in json.dumps(body, ensure_ascii=False)


def test_rawdata_unchanged(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    rawdata = created["rawdata_dir"]
    before = {str(p) for p in Path(rawdata).rglob("*")} if Path(rawdata).exists() else set()
    client.post(f"/api/projects/{created['project_id']}/motion-qc/metrics-draft")
    after = {str(p) for p in Path(rawdata).rglob("*")} if Path(rawdata).exists() else set()
    assert before == after
