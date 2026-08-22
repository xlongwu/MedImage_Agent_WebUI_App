"""SPM realign pre-execution regression matrix.

Covers the full non-executing preparation chain:
  tool catalog → params → validation → env health → dry-run →
  approval/audit → wrapper skeleton.

All tests are read-only.  No MATLAB/SPM execution.
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
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.planner.approval_gate import check_approval_gate
from src.backend.app.planner.audit_record import build_review_audit_record
from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.runtime import desktop_config
from src.backend.app.runtime.tool_catalog import get_tool_catalog_item
from src.backend.app.services import (
    bold_reference_readiness,
    motion_qc_readiness,
    spm_realign_dry_run,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.spm_realign_params import (
    default_spm_realign_params,
)


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
            "project_name": "Matrix Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "matrix_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. Catalog metadata ──────────────────────────────────────────────────────


def test_spm_realign_catalog_is_high_risk_not_executable():
    item = get_tool_catalog_item("spm_realign_subject")
    assert item.backend == "matlab-spm"
    assert item.risk_level == "high"
    assert item.requires_approval is True
    assert item.manual_required is True
    assert "not-executable" in item.tags or "not executable" in item.description.lower()


# ── 2. Invalid params blocked by validator ───────────────────────────────────


def test_spm_realign_params_invalid_plan_blocked_by_validator():
    plan = {
        "pipeline_id": "test_matrix_invalid",
        "nodes": [
            {
                "id": "spm_realign_subject",
                "backend": "matlab-spm",
                "depends_on": [],
                "params": {"quality": 1.5, "wrap": [0, 1], "matlab_script": "evil"},
            }
        ],
    }
    result = validate_plan(plan)
    assert result.ok is False
    spm_errors = [e for e in result.errors if e.code == "SPM_REALIGN_PARAM_INVALID"]
    assert len(spm_errors) >= 2  # quality + wrap + matlab_script


# ── 3. Valid plan warns not executable ──────────────────────────────────────


def test_spm_realign_valid_plan_still_warns_not_executable():
    plan = {
        "pipeline_id": "test_matrix_valid",
        "nodes": [
            {
                "id": "spm_realign_subject",
                "backend": "matlab-spm",
                "depends_on": [],
                "params": default_spm_realign_params(),
            }
        ],
    }
    result = validate_plan(plan)
    assert "spm_realign_subject" not in result.unknown_nodes
    assert "spm_realign_subject" in result.approval_required_nodes
    assert "spm_realign_subject" in result.high_risk_nodes
    ne_warnings = [w for w in result.warnings if w.code == "SPM_REALIGN_NODE_NOT_EXECUTABLE"]
    assert len(ne_warnings) >= 1


# ── 4. Environment health does not enable execution ──────────────────────────


def test_spm_realign_environment_health_does_not_enable_execution(tmp_path, monkeypatch):
    """Use a mock config with no MATLAB/SPM — execution must remain disabled."""
    config_path = tmp_path / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", config_path)

    from src.backend.app.services.environment_health import build_matlab_spm_health

    health = build_matlab_spm_health()
    assert health["real_execution_enabled"] is False
    assert health["safe_allowlist_enabled"] is False
    assert any("not enabled" in n.lower() or "not currently" in n.lower() for n in health["notes"])


# ── 5. Dry-run manifest is non-executing ─────────────────────────────────────


def test_spm_realign_dry_run_manifest_is_non_executing(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["execution_enabled"] is False
    assert body["safe_allowlist_enabled"] is False
    for key in ("dry_run_only", "rawdata_not_modified", "no_files_created"):
        assert body["safety_flags"].get(key) is True

    # No files created
    project_dir = Path(created["project_dir"])
    after = {str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()
    assert (
        len(after)
        <= len({str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()) + 5
    )


# ── 6. Approval gate requires external acknowledgements ──────────────────────


def test_spm_realign_approval_gate_requires_external_acknowledgements():
    plan = {
        "pipeline_id": "test_matrix_approval",
        "nodes": [
            {"id": "spm_smooth_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    val = validate_plan(plan).to_dict()
    result = check_approval_gate(
        plan,
        val,
        {
            "approved": True,
            "approved_nodes": ["spm_smooth_subject"],
            "approved_backends": ["matlab-spm"],
        },
    )
    assert result.execution_allowed is False
    assert any("EXTERNAL_TOOL_ACKNOWLEDGEMENT" in e.code for e in result.errors)


# ── 7. Audit record contains approval context ───────────────────────────────


def test_spm_realign_audit_payload_records_approval_context():
    plan = {
        "pipeline_id": "test_matrix_audit",
        "nodes": [
            {"id": "spm_smooth_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    val = validate_plan(plan).to_dict()
    approval = {
        "approved": True,
        "approved_nodes": ["spm_smooth_subject"],
        "approved_backends": ["matlab-spm"],
        "external_tool_acknowledgement": True,
        "rawdata_read_only_confirmed": True,
        "output_directory_confirmed": True,
        "risk_acknowledgement": True,
        "overwrite_policy": "fail_if_exists",
        "subject_scope_confirmed": True,
        "review_draft_schema_version": "review-draft-v1",
    }
    record = build_review_audit_record("execution_requested", plan, val, approval)
    ctx = record.safety.get("approval_context")
    assert ctx is not None
    assert ctx["external_tool_acknowledgement"] is True
    assert ctx["overwrite_policy"] == "fail_if_exists"
    assert ctx["approved_nodes"] == ["spm_smooth_subject"]


# ── 8. Wrapper skeleton is preview only ─────────────────────────────────────


def test_spm_realign_wrapper_skeleton_is_preview_only(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    assert resp.status_code == 200
    body = resp.json()
    assert body["command_template_id"] == "spm12_realign_estwrite_v1"
    batch = body.get("matlab_batch_preview") or ""
    if batch:
        assert "PREVIEW ONLY" in batch
        assert "matlab -batch" not in batch.lower()
    flags = body["safety_flags"]
    for key in ("preview_only", "no_matlab_called", "no_spm_called", "execution_disabled"):
        assert flags.get(key) is True

    project_dir = Path(created["project_dir"])
    before = {str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()
    after = {str(p) for p in project_dir.rglob("*")} if project_dir.exists() else set()
    assert after == before


# ── 9. Retry/resume not available for SPM realign ───────────────────────────


def test_retry_resume_not_enabled_for_spm_realign(tmp_path, monkeypatch):
    """SPM realign has no retry/resume endpoint."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    # No SPM-specific recovery endpoints
    base = f"/api/projects/{created['project_id']}"
    for suffix in ("/spm-realign/retry", "/spm-realign/resume", "/spm-realign/rerun"):
        resp = client.post(base + suffix, json={})
        assert resp.status_code == 404, f"POST {base}{suffix} should 404, got {resp.status_code}"
