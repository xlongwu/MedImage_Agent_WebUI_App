"""FunRaw/T1Raw DICOM detector tests.

Validates path-based FunRaw/T1Raw layout detection, conversion dry-run
mapping integration, and data readiness DICOM reporting.
"""

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
from src.backend.app.api.dependencies import get_project_store
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services import (
    bold_reference_readiness,
    conversion_planner,
    data_readiness,
    dicom_conversion_execution,
    motion_qc_readiness,
)
from src.backend.app.services.funraw_t1raw_detector import (
    _normalize_subject_id,
    detect_funraw_t1raw_layout,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_funraw_t1raw_fixture(tmp_path: Path) -> Path:
    """Create a minimal FunRaw/T1Raw DICOM fixture."""
    root = tmp_path / "DemoData"
    root.mkdir()

    funraw = root / "FunRaw"
    funraw.mkdir()
    t1raw = root / "T1Raw"
    t1raw.mkdir()

    for i in (1, 2):
        sub_fun = funraw / f"Sub_00{i}"
        sub_fun.mkdir()
        for j in range(1, 3 if i == 1 else 2):
            (sub_fun / f"00000{j}.dcm").write_text(
                f"mock DICOM fun sub{i} file{j}", encoding="utf-8"
            )

        sub_t1 = t1raw / f"Sub_00{i}"
        sub_t1.mkdir()
        (sub_t1 / "000001.dcm").write_text(f"mock DICOM t1 sub{i}", encoding="utf-8")

    return root


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "db.sqlite")
    monkeypatch.setitem(app.dependency_overrides, get_project_store, lambda: store)
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
        conversion_planner,
        data_readiness,
        dicom_conversion_execution,
        mock_store_module,
    ):
        monkeypatch.setattr(mod, "mock_store", store)
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _create_project(client: TestClient, tmp_path: Path, rawdata: Path) -> dict:
    pj = tmp_path / "project"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "FunRaw T1Raw Test",
            "rawdata_dir": str(rawdata),
            "project_dir": str(pj),
            "overwrite": True,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════
# Group 1 — Detector unit tests
# ═══════════════════════════════════════════════════════════════════════


def test_detector_finds_funraw_t1raw_layout(tmp_path):
    root = _make_funraw_t1raw_fixture(tmp_path)
    result = detect_funraw_t1raw_layout(str(root))
    assert result["layout_type"] == "funraw_t1raw"
    assert result["has_funraw"] is True
    assert result["has_t1raw"] is True


def test_detector_counts_subjects(tmp_path):
    root = _make_funraw_t1raw_fixture(tmp_path)
    result = detect_funraw_t1raw_layout(str(root))
    assert result["subject_count"] == 2
    assert "sub-001" in result["subject_ids"]
    assert "sub-002" in result["subject_ids"]


def test_detector_counts_dicom_files(tmp_path):
    root = _make_funraw_t1raw_fixture(tmp_path)
    result = detect_funraw_t1raw_layout(str(root))
    assert result["dicom_file_count"] == 5  # FunRaw: 2+1, T1Raw: 1+1
    assert result["nifti_file_count"] == 0


def test_detector_series_count(tmp_path):
    root = _make_funraw_t1raw_fixture(tmp_path)
    result = detect_funraw_t1raw_layout(str(root))
    assert (
        result["series_count"] == 4
    )  # FunRaw/Sub_001 + FunRaw/Sub_002 + T1Raw/Sub_001 + T1Raw/Sub_002


def test_detector_normalizes_subject_ids(tmp_path):
    root = _make_funraw_t1raw_fixture(tmp_path)
    result = detect_funraw_t1raw_layout(str(root))
    records = result["per_subject_modality"]
    # Check all subject IDs are BIDS-normalized
    for r in records:
        assert r["subject_id"].startswith("sub-"), (
            f"Expected BIDS-style subject, got: {r['subject_id']}"
        )


def test_detector_maps_funraw_to_bold(tmp_path):
    root = _make_funraw_t1raw_fixture(tmp_path)
    result = detect_funraw_t1raw_layout(str(root))
    fun_records = [r for r in result["per_subject_modality"] if r["root_name"] == "FunRaw"]
    assert len(fun_records) == 2
    for r in fun_records:
        assert r["suggested_suffix"] == "bold"
        assert r["suggested_modality_dir"] == "func"


def test_detector_maps_t1raw_to_t1w(tmp_path):
    root = _make_funraw_t1raw_fixture(tmp_path)
    result = detect_funraw_t1raw_layout(str(root))
    t1_records = [r for r in result["per_subject_modality"] if r["root_name"] == "T1Raw"]
    assert len(t1_records) == 2
    for r in t1_records:
        assert r["suggested_suffix"] == "T1w"
        assert r["suggested_modality_dir"] == "anat"


def test_detector_non_dicom_dir_returns_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = detect_funraw_t1raw_layout(str(empty))
    assert result["layout_type"] == ""
    assert result["dicom_file_count"] == 0


def test_normalize_subject_id():
    assert _normalize_subject_id("Sub_001") == "sub-001"
    assert _normalize_subject_id("sub_001") == "sub-001"
    assert _normalize_subject_id("Subject_002") == "sub-002"
    assert _normalize_subject_id("SUB_003") == "sub-003"


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Conversion dry-run integration
# ═══════════════════════════════════════════════════════════════════════


def test_conversion_dry_run_creates_mapping_for_funraw_t1raw(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"include_dicom": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] in ("ready", "warning"), (
        f"Got status: {data['status']}, blocking: {data.get('blocking_issues')}"
    )
    assert len(data["mapping_preview"]) == 4  # 2 FunRaw subjects + 2 T1Raw subjects


def test_conversion_preflight_keeps_funraw_t1raw_mappings(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    dry_run_resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"include_dicom": True},
    )
    assert dry_run_resp.status_code == 200, dry_run_resp.text
    assert len(dry_run_resp.json()["mapping_preview"]) == 4

    preflight_resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/preflight",
    )
    assert preflight_resp.status_code == 200, preflight_resp.text
    data = preflight_resp.json()
    assert data["mapping_count"] == 4
    assert len(data["mappings"]) == 4
    assert len(data["command_templates"]) == 4
    assert {m["modality"] for m in data["mappings"]} == {"func", "anat"}


def test_conversion_dry_run_maps_funraw_to_func(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"include_dicom": True},
    )
    data = resp.json()
    func_mappings = [m for m in data["mapping_preview"] if m["modality"] == "func"]
    assert len(func_mappings) == 2  # FunRaw: Sub_001 + Sub_002
    for m in func_mappings:
        assert m["suffix"] == "bold"


def test_conversion_dry_run_maps_t1raw_to_anat(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"include_dicom": True},
    )
    data = resp.json()
    anat_mappings = [m for m in data["mapping_preview"] if m["modality"] == "anat"]
    assert len(anat_mappings) == 2
    for m in anat_mappings:
        assert m["suffix"] == "T1w"


def test_conversion_dry_run_confidence_high(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run",
        json={"include_dicom": True},
    )
    data = resp.json()
    for m in data["mapping_preview"]:
        assert m["confidence"] == "high"


# ═══════════════════════════════════════════════════════════════════════
# Group 3 — Data readiness integration
# ═══════════════════════════════════════════════════════════════════════


def test_data_readiness_reports_dicom_count(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["dicom_file_count"] == 5
    assert data["dicom_series_count"] == 4


def test_data_readiness_reports_subject_count(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    data = resp.json()
    assert data["subject_count"] == 2


def test_data_readiness_detects_dicom_layout(tmp_path, monkeypatch):
    """Data readiness detects FunRaw/T1Raw and reports dicom counts
    with status=warning, not blocked."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.get(f"/api/projects/{created['project_id']}/data-readiness")
    data = resp.json()
    assert data["dicom_file_count"] > 0
    assert data["subject_count"] > 0
    # DICOM-only project with FunRaw/T1Raw layout should be warning, not blocked
    assert data["status"] == "warning", f"Expected warning, got {data['status']}"
    # Should NOT have image validation error
    errors_text = " ".join(data.get("errors", []))
    assert "image validation failed" not in errors_text.lower()
    # Should recommend Conversion Dry-Run
    next_actions_text = " ".join(data.get("next_actions", []))
    assert "conversion" in next_actions_text.lower()
    # Check that the FunRaw warning is present
    warnings_text = " ".join(data.get("warnings", []))
    assert "funraw" in warnings_text.lower() or "dicom" in warnings_text.lower()


# ═══════════════════════════════════════════════════════════════════════
# Group 4 — NIfTI QC still clean (no synthetic fallback)
# ═══════════════════════════════════════════════════════════════════════


def test_nifti_qc_still_zero_for_dicom_only(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)
    created = _create_project(client, tmp_path, root)

    resp = client.get(f"/api/projects/{created['project_id']}/nifti-qc/snapshot")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["image_count"] == 0
    # No synthetic paths
    for img in data.get("images", []):
        assert "synthetic" not in str(img.get("path", "")).lower()
    # Should have a warning about no NIfTI
    warnings_text = " ".join(data.get("warnings", []))
    assert "no nifti" in warnings_text.lower() or "conversion" in warnings_text.lower()
    # Warning count should reflect the top-level warning
    assert data["warning_count"] >= 1, f"Expected warning_count >= 1, got {data['warning_count']}"


def test_rawdata_mtime_unchanged(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    root = _make_funraw_t1raw_fixture(tmp_path)

    sentinel = root / "FunRaw" / "Sub_001" / "000001.dcm"
    mtime_before = sentinel.stat().st_mtime

    created = _create_project(client, tmp_path, root)

    # Call multiple read-only endpoints
    client.get(f"/api/projects/{created['project_id']}/data-readiness")
    client.post(
        f"/api/projects/{created['project_id']}/conversion/dry-run", json={"include_dicom": True}
    )
    client.get(f"/api/projects/{created['project_id']}/nifti-qc/snapshot")

    mtime_after = sentinel.stat().st_mtime
    assert mtime_before == mtime_after, f"Rawdata mtime changed: {mtime_before} → {mtime_after}"
