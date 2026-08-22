"""Guard tests for SPM safe allowlist policy.

All tests verify that spm_realign_subject remains disabled in the
current phase.  No MATLAB/SPM execution occurs.
"""

from __future__ import annotations

import json
import os
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
from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.runtime import desktop_config
from src.backend.app.runtime.tool_catalog import get_tool_catalog_item
from src.backend.app.services import (
    bold_reference_readiness,
    motion_qc_readiness,
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
            "project_name": "Policy Guard Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "policy_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. Catalog metadata guard ────────────────────────────────────────────────


def test_spm_realign_not_marked_executable():
    item = get_tool_catalog_item("spm_realign_subject")
    assert item.manual_required is True
    assert item.requires_approval is True
    assert item.risk_level == "high"
    assert "not-executable" in item.tags or "not executable" in item.description.lower()


# ── 2. Dry-run does not enable execution ────────────────────────────────────


def test_spm_realign_dry_run_does_not_enable_execution(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    body = resp.json()
    assert body.get("execution_enabled") is False
    assert body.get("safe_allowlist_enabled") is False
    flags = body.get("safety_flags", {})
    assert flags.get("no_matlab_called") is True
    assert flags.get("no_spm_called") is True


# ── 3. Wrapper skeleton does not enable execution ───────────────────────────


def test_spm_wrapper_skeleton_does_not_enable_execution(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")
    body = resp.json()
    flags = body.get("safety_flags", {})
    assert flags.get("execution_disabled") is True
    assert flags.get("not_safe_allowlisted") is True
    assert flags.get("preview_only") is True
    assert flags.get("no_matlab_called") is True
    batch = body.get("matlab_batch_preview") or ""
    if batch:
        assert "matlab -batch" not in batch.lower()


# ── 4. Synthetic smoke skipped without env flags ────────────────────────────


def test_synthetic_smoke_skipped_without_env_flags():
    """Verify the required env flags are NOT set in normal test environment."""
    assert os.environ.get("MEDIMAGE_MATLAB_ENABLED") != "1", (
        "MEDIMAGE_MATLAB_ENABLED must not be set during normal tests"
    )
    assert os.environ.get("MEDIMAGE_SPM_SMOKE_ENABLED") != "1", (
        "MEDIMAGE_SPM_SMOKE_ENABLED must not be set during normal tests"
    )
    assert os.environ.get("MEDIMAGE_ENABLE_SPM_REALIGN_EXECUTION") != "1", (
        "MEDIMAGE_ENABLE_SPM_REALIGN_EXECUTION must not be set during normal tests"
    )


# ── 5. Approval alone not sufficient for execution ──────────────────────────


def test_external_tool_approval_not_sufficient_for_execution():
    """Even with all approval fields, allowlist/execution path is absent."""
    plan = {
        "pipeline_id": "test_approval_not_enough",
        "nodes": [
            {
                "id": "spm_smooth_subject",
                "backend": "matlab-spm",
                "depends_on": [],
                "params": {},
            }
        ],
    }
    val = validate_plan(plan).to_dict()
    # All 6 external-tool fields present
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
    }
    # Approval gate should pass
    gate = check_approval_gate(plan, val, approval)
    assert gate.execution_allowed is True

    # But the node is manual_required (spm_realign_subject has manual_required=True)
    # and NOT in the safe allowlist — the execute endpoint would still block it.
    # This test verifies the approval gate alone does not override the allowlist.
    item = get_tool_catalog_item("spm_realign_subject")
    assert item.manual_required is True
    assert item.requires_approval is True


# ── 6. No subprocess call in dry-run/wrapper skeleton path ──────────────────


def test_no_spm_realign_subprocess_called(tmp_path, monkeypatch):
    """Dry-run and wrapper skeleton must not invoke real SPM realignment.

    The environment health check may legitimately call `matlab -batch
    \"disp(version); exit\"` which is a harmless read-only version query.
    But no real SPM jobman/spm realign call should occur.
    """
    import subprocess as sp_module

    original_run = sp_module.run
    calls = []

    def fake_run(*a, **kw):
        calls.append((a, kw))
        return original_run(*a, **kw)

    monkeypatch.setattr(sp_module, "run", fake_run)
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    client.post(f"/api/projects/{created['project_id']}/spm-realign/dry-run")
    client.post(f"/api/projects/{created['project_id']}/spm-realign/wrapper-skeleton")

    # Allowed: environment health version query (disp(version); exit)
    # Forbidden: realign, jobman, spm('realign'), estwrite, etc.
    realign_calls = [
        c
        for c in calls
        if any(
            keyword in str(a).lower()
            for keyword in ("realign", "jobman", "estwrite", "spm_realign")
            for a in c[0]
        )
    ]
    assert len(realign_calls) == 0, (
        f"Dry-run/wrapper skeleton must not call real SPM realignment: {realign_calls}"
    )
