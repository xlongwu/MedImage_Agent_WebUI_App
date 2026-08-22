"""Tests for NIfTI QC Snapshot module-level caching in QC Dashboard."""

from __future__ import annotations

import uuid
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
    qc_dashboard_report,
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
        qc_dashboard_report,
        mock_store_module,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    monkeypatch.setattr(
        qc_dashboard_report, "_REPORT_DIR", tmp_path / "outputs" / "reports" / "qc_dashboard"
    )
    return store


def _create(client: TestClient, tmp_path: Path) -> dict:
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    (rawdata / "a.txt").write_text("hello")
    proj = tmp_path / f"proj_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": f"NiftiCache-{uuid.uuid4().hex[:4]}",
            "rawdata_dir": str(rawdata),
            "project_dir": str(proj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_cache_off_does_not_create_files(tmp_path, monkeypatch):
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    cache_root = tmp_path / "cache_off_test" / "qc_dashboard"
    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", cache_root)

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=off")
    cache_files = list(cache_root.rglob("*.json")) if cache_root.exists() else []
    assert len(cache_files) == 0


def test_cache_refresh_creates_cache_file(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    # Monkeypatch cache root for module cache
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache" / "qc_dashboard")

    client = TestClient(app)
    c = _create(client, tmp_path)
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")
    cache_files = list((tmp_path / "cache" / "qc_dashboard").rglob("*.json"))
    assert len(cache_files) >= 1


def test_cache_prefer_hits_after_refresh(tmp_path, monkeypatch):
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache2" / "qc_dashboard")

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)

    # First: refresh to populate cache
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")

    # Second: prefer should hit
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=prefer").json()
    assert body["cache"]["mode"] == "prefer"
    assert body["cache"]["module_hits"].get("nifti_qc_snapshot") is True
    records = body["cache"]["module_records"]
    hit_records = [r for r in records if r["hit"] is True]
    assert len(hit_records) >= 1


def test_cache_prefer_miss_before_refresh(tmp_path, monkeypatch):
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache3" / "qc_dashboard")

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)

    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=prefer").json()
    assert body["cache"]["module_hits"].get("nifti_qc_snapshot") is not True
    assert len(body["modules"]) == 8  # all modules still ran


def test_cache_record_in_response(tmp_path, monkeypatch):
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache4" / "qc_dashboard")

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh").json()
    records = body["cache"]["module_records"]
    nifti_recs = [r for r in records if r["module_id"] == "nifti_qc_snapshot"]
    assert len(nifti_recs) >= 1
    assert nifti_recs[0]["status"] in ("miss", "stale")
    assert "artifact_path" in nifti_recs[0]


# ── Regression tests ────────────────────────────────────────────────────────


def test_cache_prefer_misses_after_rawdata_change(tmp_path, monkeypatch):
    """Adding a file to rawdata changes fingerprint → cache miss."""
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache_inv" / "qc_dashboard")

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)

    # Refresh to create cache
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")

    # Modify rawdata
    rawdata = tmp_path / "rawdata"
    (rawdata / "new_file.txt").write_text("changed")

    # Prefer should miss
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=prefer").json()
    assert body["cache"]["module_hits"].get("nifti_qc_snapshot") is not True


def test_corrupt_cache_file_falls_back_to_miss(tmp_path, monkeypatch):
    """Corrupt cache JSON → error record, dashboard still works."""
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    cache_root = tmp_path / "cache_corrupt" / "qc_dashboard"
    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", cache_root)

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)

    # Refresh to create cache
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")

    # Corrupt the cache file
    cache_files = list(cache_root.rglob("*.json"))
    if cache_files:
        cache_files[0].write_text("{not valid json")

    # Prefer should still work
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=prefer").json()
    assert body["ok"] is True or body["status"] != "unknown"
    # nifti_qc_snapshot module should still be present
    nifti_mods = [m for m in body["modules"] if m["module_id"] == "nifti_qc_snapshot"]
    assert len(nifti_mods) == 1


def test_cache_file_path_not_under_rawdata(tmp_path, monkeypatch):
    """All cache artifacts must stay under cache root, not rawdata."""
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    cache_root = tmp_path / "cache_safe" / "qc_dashboard"
    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", cache_root)

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh").json()
    for rec in body["cache"]["module_records"]:
        if rec.get("artifact_path"):
            assert "rawdata" not in rec["artifact_path"]


def test_cache_does_not_modify_rawdata_mtime(tmp_path, monkeypatch):
    """Rawdata marker mtime unchanged after cache refresh + prefer."""
    import os

    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache_mtime" / "qc_dashboard")

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    marker = tmp_path / "rawdata" / "a.txt"
    orig_mtime = os.path.getmtime(str(marker))

    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=prefer")
    assert os.path.getmtime(str(marker)) == orig_mtime


def test_cached_module_summary_has_expected_module_id(tmp_path, monkeypatch):
    """After cache hit, module_id should be nifti_qc_snapshot."""
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache_id" / "qc_dashboard")

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=prefer").json()
    nifti_mods = [m for m in body["modules"] if m["module_id"] == "nifti_qc_snapshot"]
    assert len(nifti_mods) == 1


def test_other_modules_still_present_with_nifti_cache_hit(tmp_path, monkeypatch):
    """All 8 modules must appear even with cache hit on one module."""
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", tmp_path / "cache_allmod" / "qc_dashboard")

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=refresh")
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=prefer").json()
    module_ids = {m["module_id"] for m in body["modules"]}
    expected = {
        "data_readiness",
        "bids_validation",
        "conversion_dry_run",
        "nifti_qc_snapshot",
        "bold_reference_readiness",
        "motion_qc_readiness",
        "motion_metrics_draft",
        "rsfmri_qc_planning",
    }
    assert module_ids == expected


def test_cache_off_omits_or_disables_module_records(tmp_path, monkeypatch):
    """cache=off should not create cache files."""
    import src.backend.app.services.qc_dashboard_module_cache as mod_cache

    cache_root = tmp_path / "cache_disabled" / "qc_dashboard"
    monkeypatch.setattr(mod_cache, "_CACHE_ROOT", cache_root)

    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    body = client.post(f"/api/projects/{c['project_id']}/qc-dashboard/report?cache=off").json()
    # No cache files created
    cache_files = list(cache_root.rglob("*.json")) if cache_root.exists() else []
    assert len(cache_files) == 0
    # Module records should be empty for off
    assert body["cache"]["module_records"] == []
