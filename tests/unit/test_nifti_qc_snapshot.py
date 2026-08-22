"""Tests for GET /api/projects/{project_id}/nifti-qc/snapshot."""

from __future__ import annotations

import json
import os
import uuid
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
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services import (
    bold_reference_readiness,
    motion_qc_readiness,
    nifti_qc_snapshot,
    qc_evidence_roots,
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
        nifti_qc_snapshot,
        qc_evidence_roots,
        spm_realign_dry_run,
        spm_realign_wrapper_skeleton,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _create(client: TestClient, tmp_path: Path, rawdata: Path, suffix: str = "") -> dict:
    tag = suffix or uuid.uuid4().hex[:8]
    proj = tmp_path / f"proj_{tag}"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": f"QC-{tag}",
            "rawdata_dir": str(rawdata),
            "project_dir": str(proj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 404 ──────────────────────────────────────────────────────────────────────


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/nifti-qc/snapshot")
    assert resp.status_code == 404


# ── Synthetic NIfTI tests ───────────────────────────────────────────────────


def test_synthetic_3d_reports_dimensions(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata3d"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(8, 10, 12).astype(np.float32), np.eye(4))
    nib.save(img, str(rawdata / "t3d.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "3d")
    body = client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot").json()
    assert body["readable_count"] >= 1
    img0 = body["images"][0]
    assert len(img0["dimensions"]) >= 3


def test_synthetic_4d_reports_volume_count(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata4d"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(5, 5, 5, 4).astype(np.int16), np.eye(4))
    nib.save(img, str(rawdata / "t4d.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "4d")
    body = client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot").json()
    assert body["four_d_count"] >= 1


def test_safety_flags_all_true(tmp_path, monkeypatch):
    rawdata = tmp_path / "ra_sf"
    rawdata.mkdir()
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "sf")
    flags = client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot").json()["safety_flags"]
    for key in ("read_only", "rawdata_not_modified", "no_preprocessing_executed"):
        assert flags.get(key) is True


def test_empty_rawdata_returns_wellformed(tmp_path, monkeypatch):
    """Even with empty rawdata, the fallback may find other NIfTI files.
    The endpoint must return a well-formed response regardless."""
    rawdata = tmp_path / "empty_dir"
    rawdata.mkdir()
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "emp")
    body = client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot").json()
    assert "status" in body
    assert isinstance(body["image_count"], int)


def test_registered_converted_bids_is_used_when_rawdata_has_no_nifti(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "dicom_rawdata"
    rawdata.mkdir()
    converted = tmp_path / "converted_bids"
    bold_dir = converted / "sub-001" / "func"
    bold_dir.mkdir(parents=True)
    bold_path = bold_dir / "sub-001_task-rest_bold.nii.gz"
    img = nib.Nifti1Image(np.random.randn(4, 4, 4, 5).astype(np.float32), np.eye(4))
    nib.save(img, str(bold_path))

    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "converted")
    project = store.get_project(c["project_id"])
    assert project is not None
    metadata = dict(project.metadata or {})
    metadata["preprocessing_input_dir"] = str(converted)
    metadata["converted_bids_dir"] = str(converted)
    updated = project.model_copy(update={"metadata": metadata})
    store.add_project(updated, health_status="Review", rawdata_dir=str(rawdata), overwrite=True)

    body = client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot").json()
    assert body["image_count"] == 1
    assert body["four_d_count"] == 1
    assert body["warnings"] == []
    assert body["images"][0]["path"] == str(bold_path.resolve())


def test_registered_input_scope_excludes_rawdata_duplicates_and_run_derivatives(
    tmp_path, monkeypatch
):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata"
    converted = tmp_path / "converted_bids"
    derivatives = tmp_path / "project" / "preprocessing_native_runs" / "run-foreign"
    for root in (rawdata, converted, derivatives):
        root.mkdir(parents=True)
    image = nib.Nifti1Image(np.ones((3, 3, 3, 4), dtype=np.float32), np.eye(4))
    raw_path = rawdata / "sub-001_task-rest_bold.nii.gz"
    input_path = converted / "sub-001_task-rest_bold.nii.gz"
    derivative_path = derivatives / "sub-001_desc-filtered_bold.nii.gz"
    nib.save(image, str(raw_path))
    nib.save(image, str(input_path))
    nib.save(image, str(derivative_path))

    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create(client, tmp_path, rawdata, "scoped")
    project = store.get_project(created["project_id"])
    assert project is not None
    metadata = dict(project.metadata or {})
    metadata.update(
        {
            "project_dir": str(tmp_path / "project"),
            "preprocessing_input_dir": str(converted),
            "converted_bids_dir": str(converted),
        }
    )
    store.add_project(
        project.model_copy(update={"metadata": metadata}),
        health_status="Review",
        rawdata_dir=str(rawdata),
        overwrite=True,
    )

    body = client.get(f"/api/projects/{created['project_id']}/nifti-qc/snapshot").json()

    assert body["image_count"] == 1
    assert [item["path"] for item in body["images"]] == [str(input_path.resolve())]
    assert str(raw_path.resolve()) not in {item["path"] for item in body["images"]}
    assert str(derivative_path.resolve()) not in {item["path"] for item in body["images"]}


def test_ignores_arbitrary_path(tmp_path, monkeypatch):
    rawdata = tmp_path / "ra_ap"
    rawdata.mkdir()
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "ap")
    resp = client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot?image_path=../../etc")
    assert resp.status_code == 200


def test_no_files_created(tmp_path, monkeypatch):
    rawdata = tmp_path / "ra_nf"
    rawdata.mkdir()
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "nf")
    before = {str(p) for p in tmp_path.rglob("*")}
    client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot")
    after = {str(p) for p in tmp_path.rglob("*")}
    assert after == before


def test_no_rawdata_modified(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "ra_nrm"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(4, 5, 6).astype(np.float32), np.eye(4))
    nii_path = rawdata / "test_nrm.nii.gz"
    nib.save(img, str(nii_path))
    orig_mtime = os.path.getmtime(str(nii_path))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata, "nrm")
    client.get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot")
    assert os.path.getmtime(str(nii_path)) == orig_mtime
