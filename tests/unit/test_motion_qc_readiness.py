"""Tests for GET /api/projects/{project_id}/motion-qc/readiness."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import src.backend.app.services.mock_store as mock_store_module
from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import pipeline_presets, project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services import motion_qc_readiness, qc_evidence_roots
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
        motion_qc_readiness,
        qc_evidence_roots,
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
            "project_name": "Motion QC Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "motion_qc_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/motion-qc/readiness")
    assert resp.status_code == 404


def test_created_project_returns_structured_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/motion-qc/readiness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["status"] in ("ready", "warning", "blocked", "unknown")
    assert isinstance(body["candidates"], list)
    assert isinstance(body["safety_flags"], dict)


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/motion-qc/readiness")
    flags = resp.json()["safety_flags"]
    assert flags.get("read_only") is True
    assert flags.get("rawdata_not_modified") is True
    assert flags.get("no_realign_executed") is True
    assert flags.get("no_external_tools_executed") is True
    assert flags.get("planning_only") is True


def test_candidate_fields_present(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/motion-qc/readiness")
    body = resp.json()
    for c in body["candidates"]:
        assert "subject_id" in c
        assert "bold_path" in c
        assert "has_sidecar" in c
        assert "has_motion_params" in c
        assert "has_fd_column" in c


def test_registered_converted_bids_and_native_motion_outputs_are_used(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    rawdata = tmp_path / "dicom_rawdata"
    rawdata.mkdir()
    project_dir = tmp_path / "proj_motion_native"
    converted = tmp_path / "converted_bids"
    bold_dir = converted / "sub-001" / "func"
    bold_dir.mkdir(parents=True)
    bold_path = bold_dir / "sub-001_task-rest_bold.nii.gz"
    img = nib.Nifti1Image(np.random.randn(5, 5, 5, 6).astype(np.float32), np.eye(4))
    nib.save(img, str(bold_path))
    bold_path.with_name("sub-001_task-rest_bold.json").write_text(
        json.dumps({"RepetitionTime": 2.0, "TaskName": "rest"}),
        encoding="utf-8",
    )

    created = client.post(
        "/api/projects/create",
        json={
            "project_name": "Native Motion QC",
            "rawdata_dir": str(rawdata),
            "project_dir": str(project_dir),
        },
    ).json()
    motion_dir = project_dir / "preprocessing_native_runs" / "pp-test" / "sub-001" / "motion_qc"
    motion_dir.mkdir(parents=True)
    fd_path = motion_dir / "sub-001_task-rest_bold_desc-framewise_displacement.tsv"
    fd_path.write_text("framewise_displacement\n0.00000000\n0.10000000\n", encoding="utf-8")
    project = store.get_project(created["project_id"])
    assert project is not None
    metadata = dict(project.metadata or {})
    metadata["preprocessing_input_dir"] = str(converted)
    metadata["converted_bids_dir"] = str(converted)
    updated = project.model_copy(update={"metadata": metadata})
    store.add_project(updated, health_status="Review", rawdata_dir=str(rawdata), overwrite=True)

    resp = client.get(f"/api/projects/{created['project_id']}/motion-qc/readiness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_count"] == 1
    assert body["missing_motion_param_count"] == 0
    assert body["fd_available_count"] == 1
    assert body["candidates"][0]["fd_source_path"] == str(fd_path.resolve())


def test_unscoped_native_motion_outputs_are_used_as_project_level_evidence(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    rawdata = tmp_path / "dicom_rawdata"
    rawdata.mkdir()
    project_dir = tmp_path / "proj_unscoped_motion"
    converted = tmp_path / "converted_bids"
    for subject in ("sub-001", "sub-002"):
        bold_dir = converted / subject / "func"
        bold_dir.mkdir(parents=True, exist_ok=True)
        bold_path = bold_dir / f"{subject}_task-rest_bold.nii.gz"
        img = nib.Nifti1Image(np.random.randn(5, 5, 5, 6).astype(np.float32), np.eye(4))
        nib.save(img, str(bold_path))
        bold_path.with_name(f"{subject}_task-rest_bold.json").write_text(
            json.dumps({"RepetitionTime": 2.0, "TaskName": "rest"}),
            encoding="utf-8",
        )

    created = client.post(
        "/api/projects/create",
        json={
            "project_name": "Unscoped Native Motion QC",
            "rawdata_dir": str(rawdata),
            "project_dir": str(project_dir),
        },
    ).json()
    motion_dir = project_dir / "preprocessing_native_runs" / "pp-test" / "artifacts" / "motion_qc"
    motion_dir.mkdir(parents=True)
    fd_path = (
        motion_dir / "slice_timing_bold_desc-motion_parameters_desc-framewise_displacement.tsv"
    )
    fd_path.write_text("framewise_displacement\n0.00000000\n0.10000000\n", encoding="utf-8")

    project = store.get_project(created["project_id"])
    assert project is not None
    metadata = dict(project.metadata or {})
    metadata["preprocessing_input_dir"] = str(converted)
    metadata["converted_bids_dir"] = str(converted)
    updated = project.model_copy(update={"metadata": metadata})
    store.add_project(updated, health_status="Review", rawdata_dir=str(rawdata), overwrite=True)

    body = client.get(f"/api/projects/{created['project_id']}/motion-qc/readiness").json()
    assert body["candidate_count"] == 2
    assert body["missing_motion_param_count"] == 0
    assert body["fd_available_count"] == 2
    assert all(c["fd_source_path"] == str(fd_path.resolve()) for c in body["candidates"])
    assert all(
        "subject linkage is not explicit" in " ".join(c["warnings"]) for c in body["candidates"]
    )
    assert any(
        "2 BOLD candidate(s) across 2 subject(s)" in action for action in body["next_actions"]
    )


def test_endpoint_ignores_arbitrary_path_query(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/motion-qc/readiness?path=../../etc")
    assert resp.status_code == 200


def test_preset_metadata_reflects_read_only_status():
    preset = pipeline_presets.get_preset("rsfmri_preproc_mvp")
    assert preset is not None
    for node in preset.nodes:
        if node.id == "rsfmri_motion_qc_plan":
            assert node.executable is False
            assert node.backend == "contract"
            assert "read-only" in " ".join(node.safety_notes).lower()
            assert "inspectable" in str(node.params)
            break
    else:
        raise AssertionError("rsfmri_motion_qc_plan not found in preset")
