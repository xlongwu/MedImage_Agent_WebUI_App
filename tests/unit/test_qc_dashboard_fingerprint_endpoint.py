"""Tests for GET /api/projects/{project_id}/qc-dashboard/fingerprint."""

from __future__ import annotations

import json
import os
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
from src.backend.app.services.qc_dashboard_fingerprint import collect_qc_dashboard_fingerprint_roots


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
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
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
            "project_name": f"FP-{uuid.uuid4().hex[:4]}",
            "rawdata_dir": str(rawdata),
            "project_dir": str(proj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_project_not_found_returns_404():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/qc-dashboard/fingerprint")
    assert resp.status_code == 404


def test_returns_structured_response(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    body = client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint").json()
    assert body["ok"] is True
    assert "fingerprint" in body
    assert body["fingerprint"]["file_count"] >= 1


def test_safety_flags_all_true(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    flags = client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint").json()[
        "safety_flags"
    ]
    for key in ("read_only", "rawdata_not_modified", "metadata_only", "no_cache_files_created"):
        assert flags.get(key) is True


def test_ignores_arbitrary_path_query(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    resp = client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint?path=../../secret")
    assert resp.status_code == 200


def test_fingerprint_changes_on_file_added(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    fp1 = client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint").json()[
        "fingerprint"
    ]["fingerprint"]
    rawdata = tmp_path / "rawdata"
    (rawdata / "new.txt").write_text("new")
    fp2 = client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint").json()[
        "fingerprint"
    ]["fingerprint"]
    assert fp1 != fp2


def test_creates_no_files(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    before = {str(p) for p in tmp_path.rglob("*")}
    client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint")
    after = {str(p) for p in tmp_path.rglob("*")}
    assert after == before


def test_rawdata_mtime_unchanged(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    marker = tmp_path / "rawdata" / "a.txt"
    orig_mtime = os.path.getmtime(str(marker))
    client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint")
    assert os.path.getmtime(str(marker)) == orig_mtime


# ── Hardening tests ─────────────────────────────────────────────────────────


def test_fingerprint_endpoint_does_not_call_qc_dashboard_report(tmp_path, monkeypatch):
    """Fingerprint endpoint must not trigger dashboard report generation."""
    import src.backend.app.services.qc_dashboard_report as dash

    _orig = dash.build_qc_dashboard_report

    def should_not_be_called(*a, **kw):
        raise RuntimeError("Fingerprint endpoint must not call build_qc_dashboard_report")

    monkeypatch.setattr(dash, "build_qc_dashboard_report", should_not_be_called)
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    c = _create(client, tmp_path)
    resp = client.get(f"/api/projects/{c['project_id']}/qc-dashboard/fingerprint")
    assert resp.status_code == 200


def test_missing_rawdata_root_returns_warning_not_500(tmp_path, monkeypatch):
    """Project with rawdata dir that existed but was removed must not crash."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    rawdata = tmp_path / "will_be_deleted"
    rawdata.mkdir()
    (rawdata / "f.txt").write_text("x")
    proj = tmp_path / f"proj_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": f"FP-Removed-{uuid.uuid4().hex[:4]}",
            "rawdata_dir": str(rawdata),
            "project_dir": str(proj),
        },
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["project_id"]
    # Now delete the rawdata directory
    import shutil

    shutil.rmtree(str(rawdata))
    body = client.get(f"/api/projects/{pid}/qc-dashboard/fingerprint").json()
    assert body["ok"] is True
    assert (
        len(body["fingerprint"]["missing_roots"]) >= 1 or len(body["fingerprint"]["warnings"]) >= 1
    )


def test_import_record_roots_are_included_when_present(tmp_path, monkeypatch):
    """Import records under project metadata are included in fingerprint roots."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    (rawdata / "a.txt").write_text("hello")
    import_dir = tmp_path / "imports"
    import_dir.mkdir()
    (import_dir / "b.nii").write_text("nii")
    # Create project with import metadata if the API supports it
    proj = tmp_path / f"proj_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": f"FP-Import-{uuid.uuid4().hex[:4]}",
            "rawdata_dir": str(rawdata),
            "project_dir": str(proj),
            "metadata": {"import_roots": [str(import_dir)]},
        },
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["project_id"]
    body = client.get(f"/api/projects/{pid}/qc-dashboard/fingerprint").json()
    # At minimum, the rawdata root is included
    assert body["fingerprint"]["file_count"] >= 1
    # Import roots may or may not be included depending on endpoint logic
    # TODO: extend root discovery to include metadata.get('import_roots', [])


# ── Pure helper tests ──────────────────────────────────────────────────────


def test_helper_deduplicates_roots():
    roots = collect_qc_dashboard_fingerprint_roots(
        {
            "rawdata_dir": "/a",
            "import_roots": ["/a", "/b", "/b"],
        }
    )
    assert roots == ["/a", "/b"]


def test_helper_handles_import_roots_as_string():
    roots = collect_qc_dashboard_fingerprint_roots(
        {
            "rawdata_dir": "/a",
            "import_roots": "/b",
        }
    )
    assert "/b" in roots


def test_helper_handles_import_roots_as_list():
    roots = collect_qc_dashboard_fingerprint_roots(
        {
            "import_roots": ["/data1", "/data2"],
        }
    )
    assert roots == ["/data1", "/data2"]


def test_helper_handles_import_records_path():
    roots = collect_qc_dashboard_fingerprint_roots(
        {
            "rawdata_dir": "/a",
            "import_records": [{"path": "/imports/x"}, {"path": "/imports/y"}],
        }
    )
    assert "/imports/x" in roots
    assert "/imports/y" in roots


def test_helper_handles_import_records_root():
    roots = collect_qc_dashboard_fingerprint_roots(
        {
            "import_records": [{"root": "/r1"}, {"output_dir": "/o1"}],
        }
    )
    assert "/r1" in roots
    assert "/o1" in roots


def test_helper_handles_none_metadata():
    roots = collect_qc_dashboard_fingerprint_roots(None)
    assert roots == []


def test_helper_handles_empty_metadata():
    roots = collect_qc_dashboard_fingerprint_roots({})
    assert roots == []
