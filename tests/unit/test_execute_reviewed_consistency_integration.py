"""Execute-reviewed consistency integration tests.

Validates that the Phase 3 verify_execution_consistency() helper
is integrated into the reviewed execution preflight path and that
hard consistency failures block before run_pipeline().
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

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
)
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.goal_contract_helpers import reviewed_goal_candidate

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
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
    ):
        monkeypatch.setattr(mod, "mock_store", store)
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _enable_env(monkeypatch) -> None:
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")


def _create_project(client: TestClient, tmp_path: Path) -> dict:
    rd = tmp_path / "rawdata"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "dataset_description.json").write_text(
        '{"Name":"test","BIDSVersion":"1.8.0"}', encoding="utf-8"
    )
    pj = tmp_path / "project"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "Consistency Test",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_plan(created: dict) -> dict:
    return {
        "pipeline_id": "test_consistency",
        "project_context": {
            "project_id": created["project_id"],
            "project_config_path": created["project_config_path"],
            "rawdata_dir": created.get("rawdata_dir", ""),
            "dataset_index_path": created.get("dataset_index_path", ""),
            "source": "created",
            "diagnostics": created.get("diagnostics", {}),
        },
        "nodes": [
            {"id": "contract_smoke", "backend": "python", "depends_on": [], "params": {}},
        ],
    }


def _save_plan(client: TestClient, created: dict, plan: dict) -> str:
    goal = "Consistency test"
    resp = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True},
            "goal": goal,
            "provider": "rule_based",
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "test-reviewer",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["reviewed_plan"]["reviewed_plan_id"]


def _execute(
    client: TestClient, created: dict, plan: dict, reviewed_plan_id: str, **overrides
) -> dict:
    reviewed = client.get(
        f"/api/projects/{created['project_id']}/plans/{reviewed_plan_id}"
    )
    assert reviewed.status_code == 200, reviewed.text
    approval_summary_hash = reviewed.json()["reviewed_plan"]["payload"][
        "approval_envelope"
    ]["summary_hash"]
    body = {
        "plan": plan,
        "approval": {
            "approved": True,
            "approved_by": "test",
            "approved_nodes": ["*"],
            "rejected_nodes": [],
            "approval_summary_hash": approval_summary_hash,
        },
        "project_id": created["project_id"],
        "reviewed_plan_id": reviewed_plan_id,
        "project_config_path": created["project_config_path"],
        "dry_run": False,
        "confirm_execution": True,
        "persist_audit": True,
        "write_pipeline_yaml": True,
    }
    body.update(overrides)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _setup_executed(tmp_path, monkeypatch, client):
    created = _create_project(client, tmp_path)
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)
    data = _execute(client, created, plan, rpid)
    return created, data


# ═══════════════════════════════════════════════════════════════════════
# Group 1 — Consistency report in response
# ═══════════════════════════════════════════════════════════════════════


def test_contract_smoke_execution_succeeds_with_consistency(tmp_path, monkeypatch):
    """contract_smoke execution still succeeds with consistency check."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, data = _setup_executed(tmp_path, monkeypatch, client)

    # Execution should succeed
    assert data["status"] in ("EXECUTION_SUBMITTED", "SUCCESS", "EXECUTION_PREFLIGHT_READY"), (
        f"Got: {data['status']}, errors: {data.get('errors')}"
    )
    assert data.get("ok") is True


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Consistency failure blocks execution
# ═══════════════════════════════════════════════════════════════════════


def test_consistency_failure_blocks_execution(tmp_path, monkeypatch):
    """Simulated consistency failure returns EXECUTION_CONSISTENCY_FAILED."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)

    # Monkeypatch verify_execution_consistency to force a failure
    from src.backend.app.schemas.execution_consistency import (
        ConsistencyIssue,
        ExecutionConsistencyReport,
    )

    fail_report = ExecutionConsistencyReport(
        ok=False,
        status="fail",
        issue_count=2,
        error_count=2,
        issues=[
            ConsistencyIssue(
                code="PROJECT_ID_MISMATCH",
                severity="error",
                message="Simulated project_id mismatch",
                expected="p1",
                actual="p2",
            ),
            ConsistencyIssue(
                code="PLAN_HASH_MISMATCH",
                severity="error",
                message="Simulated plan_hash mismatch",
            ),
        ],
        checked_fields=["project_id", "plan_hash"],
    )

    def _forced_fail(**kwargs):
        return fail_report

    monkeypatch.setattr(
        execute_reviewed_routes,
        "verify_execution_consistency",
        _forced_fail,
    )

    data = _execute(client, created, plan, rpid)
    assert data["status"] == "EXECUTION_CONSISTENCY_FAILED"
    assert data["ok"] is False
    assert data.get("execution_consistency") is not None
    ec = data["execution_consistency"]
    assert ec["status"] == "fail"
    assert ec["error_count"] == 2


def test_consistency_failure_does_not_call_executor(tmp_path, monkeypatch):
    """Consistency failure prevents run_pipeline from being called."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)

    # Force consistency failure
    from src.backend.app.schemas.execution_consistency import (
        ConsistencyIssue,
        ExecutionConsistencyReport,
    )

    fail_report = ExecutionConsistencyReport(
        ok=False,
        status="fail",
        issue_count=1,
        error_count=1,
        issues=[
            ConsistencyIssue(
                code="PROJECT_ID_MISMATCH",
                severity="error",
                message="Simulated mismatch",
            ),
        ],
        checked_fields=["project_id"],
    )

    monkeypatch.setattr(
        execute_reviewed_routes,
        "verify_execution_consistency",
        lambda **kwargs: fail_report,
    )

    # Also patch run_pipeline to detect if it's called
    called = []
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kwargs: called.append(True) or {"status": "SUCCESS"},
    )

    data = _execute(client, created, plan, rpid)
    assert data["status"] == "EXECUTION_CONSISTENCY_FAILED"
    assert len(called) == 0, "run_pipeline should NOT have been called"


# ═══════════════════════════════════════════════════════════════════════
# Group 3 — Existing gates still block before consistency
# ═══════════════════════════════════════════════════════════════════════


def test_missing_project_context_blocks_before_consistency(tmp_path, monkeypatch):
    """Missing project context still blocks — consistency not reached."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    # Plan WITHOUT project_context
    plan_no_ctx = {
        "pipeline_id": "test_no_ctx",
        "nodes": [
            {"id": "contract_smoke", "backend": "python", "depends_on": [], "params": {}},
        ],
    }

    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan_no_ctx,
            "approval": {
                "approved": True,
                "approved_by": "test",
                "approved_nodes": ["*"],
                "rejected_nodes": [],
            },
            "project_id": created["project_id"],
            "project_config_path": created["project_config_path"],
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Should be blocked by project context, not consistency
    assert data["status"] == "PROJECT_CONTEXT_MISMATCH"
    assert data.get("execution_consistency") is None


def test_approval_gate_still_blocks_before_execution(tmp_path, monkeypatch):
    """Missing confirm_execution blocks before consistency check."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)

    data = _execute(client, created, plan, rpid, confirm_execution=False)
    assert data["status"] == "CONFIRMATION_REQUIRED"


def test_external_tool_nodes_still_blocked(tmp_path, monkeypatch):
    """SPM realign dry-run shows policy warnings; execution would block."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)

    # Use spm_smooth_subject which is high-risk but doesn't require dataset_index
    plan = {
        "pipeline_id": "test_spm_blocked",
        "project_context": {
            "project_id": created["project_id"],
            "project_config_path": created["project_config_path"],
            "rawdata_dir": created.get("rawdata_dir", ""),
            "dataset_index_path": created.get("dataset_index_path", ""),
            "source": "created",
            "diagnostics": created.get("diagnostics", {}),
        },
        "nodes": [
            {"id": "contract_smoke", "backend": "python", "depends_on": [], "params": {}},
            {
                "id": "spm_smooth_subject",
                "backend": "matlab-spm",
                "depends_on": ["contract_smoke"],
                "params": {},
            },
        ],
    }

    # Dry-run to check adapter policy
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {
                "approved": True,
                "approved_by": "test",
                "approved_nodes": ["*"],
                "rejected_nodes": [],
            },
            "project_id": created["project_id"],
            "project_config_path": created["project_config_path"],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Dry-run should flag high-risk nodes
    validation = data.get("validation", {}) or {}
    high_risk = validation.get("high_risk_nodes", [])
    assert "spm_smooth_subject" in high_risk or data["status"] != "DRY_RUN_OK"


# ═══════════════════════════════════════════════════════════════════════
# Group 4 — Safety boundaries
# ═══════════════════════════════════════════════════════════════════════


def test_rawdata_not_modified(tmp_path, monkeypatch):
    """Rawdata mtime unchanged after successful consistency + execution."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)

    rd = tmp_path / "rawdata_cs"
    rd.mkdir(parents=True, exist_ok=True)
    sentinel = rd / "sentinel.txt"
    sentinel.write_text("read-only", encoding="utf-8")
    mtime_before = sentinel.stat().st_mtime

    pj = tmp_path / "pj_cs"
    create_resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "CS Safety",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)
    _execute(client, created, plan, rpid)

    mtime_after = sentinel.stat().st_mtime
    assert mtime_before == mtime_after, f"Rawdata mtime changed: {mtime_before} → {mtime_after}"


def test_no_external_subprocess_called(tmp_path, monkeypatch):
    """No MATLAB/SPM/DPABI subprocess during consistency + execution."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)

    import subprocess

    original_run = subprocess.run
    called_matlab = []

    def _patched(*args, **kwargs):
        cmd = " ".join(str(a) for a in (args[0] if args else []))
        if any(k in cmd.lower() for k in ("matlab", "spm", "dpabi")):
            called_matlab.append(cmd)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _patched)

    client = TestClient(app)
    created = _create_project(client, tmp_path)
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)
    _execute(client, created, plan, rpid)

    assert len(called_matlab) == 0, f"MATLAB/SPM called: {called_matlab}"


def test_run_history_still_visible(tmp_path, monkeypatch):
    """Run history and detail still visible after consistency integration."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, data = _setup_executed(tmp_path, monkeypatch, client)

    hist_resp = client.get(f"/api/projects/{created['project_id']}/runs")
    assert hist_resp.status_code == 200
    runs_data = hist_resp.json()
    runs = runs_data if isinstance(runs_data, list) else runs_data.get("runs", [])
    assert len(runs) >= 1

    run_id = data.get("run_id")
    if run_id:
        detail_resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}")
        assert detail_resp.status_code == 200


def test_manifest_provenance_still_written(tmp_path, monkeypatch):
    """Manifest and provenance artifacts still produced."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = data.get("run_id")
    assert run_id is not None

    artifacts_resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts")
    artifacts_data = artifacts_resp.json()
    artifacts = (
        artifacts_data if isinstance(artifacts_data, list) else artifacts_data.get("artifacts", [])
    )

    names = [a.get("name", "") for a in artifacts]
    assert "contract_smoke_output_manifest.json" in names
    assert "contract_smoke_execution_provenance.json" in names
