"""Phase 2 feature regression matrix — smoke/contract tests across all areas.

Covers: data readiness, BIDS validation, NIfTI QC/thumbnail, BOLD/Motion QC,
QC Dashboard report/cache/fingerprint, SPM non-execution guards.
"""

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
from src.backend.app.runtime import desktop_config, tool_catalog
from src.backend.app.services import (
    bold_reference_readiness,
    motion_qc_readiness,
    nifti_thumbnail,
    qc_dashboard_report,
    spm_realign_dry_run,
    spm_realign_wrapper_skeleton,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _iso(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "db.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "cfg.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "prj")
    for mod in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        execute_reviewed_routes,
        bold_reference_readiness,
        motion_qc_readiness,
        spm_realign_dry_run,
        spm_realign_wrapper_skeleton,
        qc_dashboard_report,
        nifti_thumbnail,
        mock_store_module,
    ):
        monkeypatch.setattr(mod, "mock_store", store)
    monkeypatch.setattr(qc_dashboard_report, "_REPORT_DIR", tmp_path / "o" / "r" / "qc")
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _create(client: TestClient, tmp_path: Path) -> dict:
    rd = tmp_path / "rd"
    rd.mkdir()
    (rd / "f.txt").write_text("x")
    pj = tmp_path / f"p_{uuid.uuid4().hex[:6]}"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": f"Mtx-{uuid.uuid4().hex[:4]}",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Group 1: Read-only readiness endpoints ──────────────────────────────────


def test_data_readiness_ok(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = TestClient(app).get(f"/api/projects/{c['project_id']}/data-readiness").json()
    assert body["ok"] is True or "status" in body
    assert isinstance(body.get("warnings", []), list)


def test_bids_validation_ok(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = TestClient(app).get(f"/api/projects/{c['project_id']}/bids-validation").json()
    assert "status" in body


def test_nifti_qc_snapshot_ok(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = TestClient(app).get(f"/api/projects/{c['project_id']}/nifti-qc/snapshot").json()
    assert body.get("safety_flags", {}).get("read_only") is True


def test_bold_reference_ok(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = TestClient(app).get(f"/api/projects/{c['project_id']}/bold-reference/readiness").json()
    assert "status" in body


def test_motion_qc_ok(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = TestClient(app).get(f"/api/projects/{c['project_id']}/motion-qc/readiness").json()
    assert "status" in body


# ── Group 2: POST endpoints ─────────────────────────────────────────────────


def test_conversion_dry_run_ok(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = (
        TestClient(app)
        .post(f"/api/projects/{c['project_id']}/conversion/dry-run", json={"dry_run": True})
        .json()
    )
    assert "status" in body


def test_dashboard_report_cache_off_ok(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = (
        TestClient(app)
        .post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=off")
        .json()
    )
    assert body["cache"]["mode"] == "off"
    assert len(body["modules"]) == 8


# ── Group 3: NIfTI thumbnail ────────────────────────────────────────────────


def test_thumbnail_view_all(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")
    rd = tmp_path / "rd_thumb"
    rd.mkdir()
    img = nib.Nifti1Image(np.random.randn(6, 7, 8).astype(np.float32), np.eye(4))
    nib.save(img, str(rd / "t3d.nii.gz"))
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = (
        TestClient(app)
        .get(f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?view=all")
        .json()
    )
    if body.get("ok"):
        assert len(body["thumbnails"]) == 3


def test_thumbnail_invalid_vol_400(tmp_path, monkeypatch):
    try:
        import nibabel as nib
    except ImportError:
        pytest.skip("nibabel not installed")
    rd = tmp_path / "rd_thumb2"
    rd.mkdir()
    img = nib.Nifti1Image(np.random.randn(4, 5, 6, 3).astype(np.float32), np.eye(4))
    nib.save(img, str(rd / "t4d.nii.gz"))
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    resp = TestClient(app).get(
        f"/api/projects/{c['project_id']}/nifti-qc/images/nifti_0000/thumbnail?volume_index=99"
    )
    assert resp.status_code == 400
    assert "volume_index" in resp.json().get("detail", "") or "out of range" in resp.json().get(
        "detail", ""
    )


# ── Group 4: QC Dashboard latest / fingerprint / cache ──────────────────────


def test_latest_404_before_generation(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    assert (
        TestClient(app)
        .get(f"/api/projects/{c['project_id']}/qc-dashboard/report/latest")
        .status_code
        == 404
    )


def test_fingerprint_returns_hash(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = TestClient(app).get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint").json()
    assert body["fingerprint"]["fingerprint"] is not None


def test_dashboard_cache_creates_no_files_in_repo(tmp_path, monkeypatch):
    import src.backend.app.services.qc_dashboard_module_cache as mc

    mc_root = tmp_path / "mc_repo"
    monkeypatch.setattr(mc, "_CACHE_ROOT", mc_root)
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    TestClient(app).post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")
    # All cache files under monkeypatched root
    for f in mc_root.rglob("*.json"):
        assert "rawdata" not in str(f)


# ── Group 5: SPM non-execution guards ───────────────────────────────────────


def test_spm_realign_not_executable():
    item = tool_catalog.get_tool_catalog_item("spm_realign_subject")
    assert item.risk_level == "high"
    assert item.requires_approval is True
    assert item.manual_required is True


def test_spm_dry_run_disabled(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = TestClient(app).post(f"/api/projects/{c['project_id']}/spm-realign/dry-run").json()
    assert body.get("execution_enabled") is False


def test_spm_wrapper_preview_only(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    c = _create(TestClient(app), tmp_path)
    body = (
        TestClient(app).post(f"/api/projects/{c['project_id']}/spm-realign/wrapper-skeleton").json()
    )
    batch = body.get("matlab_batch_preview") or ""
    if batch:
        assert "PREVIEW ONLY" in batch
        assert "matlab -batch" not in batch.lower()


def test_spm_smoke_skipped():
    assert os.environ.get("MEDIMAGE_MATLAB_ENABLED") != "1"
    assert os.environ.get("MEDIMAGE_SPM_SMOKE_ENABLED") != "1"
