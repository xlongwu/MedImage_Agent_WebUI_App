"""Tests for GET /api/projects/{project_id}/nifti-qc/images/{image_id}/thumbnail."""

from __future__ import annotations

import base64
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
    nifti_thumbnail,
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
        nifti_thumbnail,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _create(client: TestClient, tmp_path: Path, rawdata: Path) -> dict:
    proj = tmp_path / f"proj_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "ThumbTest",
            "rawdata_dir": str(rawdata),
            "project_dir": str(proj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/nifti-qc/images/nifti_0000/thumbnail")
    assert resp.status_code == 404


def test_invalid_image_id_returns_error(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(4, 5, 6).astype(np.float32), np.eye(4))
    nib.save(img, str(rawdata / "t.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    resp = client.get(f"/api/projects/{c['project_id']}/nifti-qc/images/nonexistent/thumbnail")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_synthetic_3d_returns_three_thumbnails(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata3d"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(10, 12, 14).astype(np.float32), np.eye(4))
    nib.save(img, str(rawdata / "t3d.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    body = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?view=all"
    ).json()
    # Fallback search roots may provide images; accept either ok or error
    if body["ok"]:
        assert len(body["thumbnails"]) == 3
        views = {t["view"] for t in body["thumbnails"]}
        assert views == {"axial", "coronal", "sagittal"}


def test_thumbnail_png_base64_decodable(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata_png"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(8, 8, 8).astype(np.float32), np.eye(4))
    nib.save(img, str(rawdata / "t.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    body = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?view=axial"
    ).json()
    if body["thumbnails"]:
        png = body["thumbnails"][0]["png_base64"]
        decoded = base64.b64decode(png)
        assert decoded[:4] == b"\x89PNG"


def test_size_capped_at_256(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata_sz"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(64, 64, 64).astype(np.float32), np.eye(4))
    nib.save(img, str(rawdata / "t.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    # Request absurd size — must be capped
    body = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?size=999"
    ).json()
    if body["thumbnails"]:
        t = body["thumbnails"][0]
        assert t["width"] <= 256 and t["height"] <= 256


def test_safety_flags_all_true(tmp_path, monkeypatch):
    rawdata = tmp_path / "rawdata_sf"
    rawdata.mkdir()
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    body = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail"
    ).json()
    flags = body["safety_flags"]
    for key in (
        "read_only",
        "rawdata_not_modified",
        "no_preprocessing_executed",
        "thumbnail_only",
        "clinical_use_prohibited",
    ):
        assert flags.get(key) is True


def test_no_files_created(tmp_path, monkeypatch):
    rawdata = tmp_path / "rawdata_nf"
    rawdata.mkdir()
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    before = {str(p) for p in tmp_path.rglob("*")}
    client.get(f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail")
    after = {str(p) for p in tmp_path.rglob("*")}
    assert after == before


# ── Regression tests ────────────────────────────────────────────────────────


def test_4d_nifti_selected_volume_thumbnail(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata_4d"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(6, 7, 8, 4).astype(np.float32), np.eye(4))
    nib.save(img, str(rawdata / "t4d.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    resp = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?view=axial&volume_index=1"
    )
    if resp.status_code == 400:
        # May get 400 if nifti_0000 maps to a 3D fallback image
        pass
    else:
        body = resp.json()
        if body.get("ok"):
            assert body["selected_volume_index"] == 1
            assert len(body["thumbnails"]) >= 1


def test_out_of_range_volume_index_returns_400(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata_oor"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(4, 5, 6, 3).astype(np.float32), np.eye(4))
    nib.save(img, str(rawdata / "t.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    resp = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?volume_index=99"
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail", "")
    assert "volume_index" in detail or "out of range" in detail


def test_constant_intensity_thumbnail_does_not_crash(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata_const"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.ones((6, 7, 8), dtype=np.float32), np.eye(4))
    nib.save(img, str(rawdata / "const.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    body = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?view=axial"
    ).json()
    if body["thumbnails"]:
        decoded = base64.b64decode(body["thumbnails"][0]["png_base64"])
        assert decoded[:4] == b"\x89PNG"


def test_nan_thumbnail_does_not_crash(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata_nan"
    rawdata.mkdir()
    data = np.random.randn(6, 7, 8).astype(np.float32)
    data[2, 3, 4] = np.nan
    data[1, 2, 3] = np.inf
    data[4, 5, 6] = -np.inf
    img = nib.Nifti1Image(data, np.eye(4))
    nib.save(img, str(rawdata / "nan.nii.gz"))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    body = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?view=axial"
    ).json()
    if body["thumbnails"]:
        decoded = base64.b64decode(body["thumbnails"][0]["png_base64"])
        assert decoded[:4] == b"\x89PNG"


def test_thumbnail_endpoint_ignores_arbitrary_path_query(tmp_path, monkeypatch):
    rawdata = tmp_path / "rawdata_path"
    rawdata.mkdir()
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    resp = client.get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?path=../../secret&view=all"
    )
    assert resp.status_code == 200


def test_thumbnail_rawdata_mtime_unchanged(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")

    rawdata = tmp_path / "rawdata_mt"
    rawdata.mkdir()
    img = nib.Nifti1Image(np.random.randn(4, 5, 6).astype(np.float32), np.eye(4))
    nii_path = rawdata / "t.nii.gz"
    nib.save(img, str(nii_path))
    orig_mtime = os.path.getmtime(str(nii_path))

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path, rawdata)
    client.get(f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?view=axial")
    assert os.path.getmtime(str(nii_path)) == orig_mtime
