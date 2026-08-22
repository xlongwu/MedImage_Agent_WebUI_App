"""Tests for GET /api/projects/{project_id}/bold-reference/readiness."""

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
from src.backend.app.services import (
    bold_reference_readiness,
    motion_qc_readiness,
    qc_evidence_roots,
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
            "project_name": "BOLD Ref Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "bold_ref_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/bold-reference/readiness")
    assert resp.status_code == 404


def test_created_project_returns_structured_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/bold-reference/readiness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project_id"] == created["project_id"]
    assert body["status"] in ("ready", "warning", "blocked", "unknown")
    assert isinstance(body["candidates"], list)


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/bold-reference/readiness")
    flags = resp.json()["safety_flags"]
    assert flags.get("read_only") is True
    assert flags.get("rawdata_not_modified") is True
    assert flags.get("no_reference_image_written") is True
    assert flags.get("no_external_tools_executed") is True
    assert flags.get("planning_only") is True


def test_candidate_fields_present(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/bold-reference/readiness")
    body = resp.json()
    for c in body["candidates"]:
        assert "subject_id" in c
        assert "bold_path" in c
        assert "volume_count" in c
        assert "is_4d" in c
        assert "has_sidecar" in c
        assert "reference_strategy" in c
        assert c["reference_strategy"] in ("middle_volume", "single_volume", "manual_required")


def test_registered_converted_bids_provides_bold_candidates(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    rawdata = tmp_path / "dicom_rawdata"
    rawdata.mkdir()
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
            "project_name": "Converted BOLD Ref",
            "rawdata_dir": str(rawdata),
            "project_dir": str(tmp_path / "proj_converted_bold"),
        },
    ).json()
    project = store.get_project(created["project_id"])
    assert project is not None
    metadata = dict(project.metadata or {})
    metadata["preprocessing_input_dir"] = str(converted)
    metadata["converted_bids_dir"] = str(converted)
    updated = project.model_copy(update={"metadata": metadata})
    store.add_project(updated, health_status="Review", rawdata_dir=str(rawdata), overwrite=True)

    resp = client.get(f"/api/projects/{created['project_id']}/bold-reference/readiness")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_count"] == 1
    assert body["ready_count"] == 1
    assert body["candidates"][0]["bold_path"] == str(bold_path.resolve())


def test_endpoint_ignores_arbitrary_path_query(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(
        f"/api/projects/{created['project_id']}/bold-reference/readiness?path=../../etc"
    )
    assert resp.status_code == 200


def test_preset_metadata_reflects_read_only_status():
    preset = pipeline_presets.get_preset("rsfmri_preproc_mvp")
    assert preset is not None
    for node in preset.nodes:
        if node.id == "rsfmri_bold_reference_check":
            assert node.executable is False
            assert node.backend == "contract"
            assert "read-only" in " ".join(node.safety_notes).lower()
            assert "inspectable" in str(node.params)
            assert "reference image" in " ".join(node.safety_notes).lower()
            break
    else:
        raise AssertionError("rsfmri_bold_reference_check not found in preset")
