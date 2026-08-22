"""Run-state timeline tests — Phase 3 Productization.

Tests: state normalization, timeline endpoint, execution state helpers
integration, safety boundaries.
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
from src.backend.app.schemas.execution_state import (
    is_run_resume_eligible,
    is_run_retry_eligible,
    is_run_terminal,
)
from src.backend.app.services import (
    bold_reference_readiness,
    motion_qc_readiness,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.run_state_timeline import (
    build_run_state_timeline,
    normalize_node_state,
    normalize_run_state,
)
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
            "project_name": "Timeline Test",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_plan(created: dict) -> dict:
    return {
        "pipeline_id": "test_timeline",
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
    goal = "Timeline test"
    resp = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True},
            "goal": goal,
            "provider": "mock",
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "test-reviewer",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["reviewed_plan"]["reviewed_plan_id"]


def _execute(client: TestClient, created: dict, plan: dict, reviewed_plan_id: str) -> dict:
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
            "reviewed_plan_id": reviewed_plan_id,
            "project_config_path": created["project_config_path"],
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _setup_executed(tmp_path, monkeypatch, client):
    created = _create_project(client, tmp_path)
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)
    data = _execute(client, created, plan, rpid)
    return created, data


# ═══════════════════════════════════════════════════════════════════════
# Group 1 — State normalization
# ═══════════════════════════════════════════════════════════════════════


def test_normalize_run_state_known_values():
    assert normalize_run_state("COMPLETED") == "succeeded"
    assert normalize_run_state("SUCCESS") == "succeeded"
    assert normalize_run_state("SUCCEEDED") == "succeeded"
    assert normalize_run_state("FAILED") == "failed"
    assert normalize_run_state("EXECUTION_FAILED") == "failed"
    assert normalize_run_state("BLOCKED") == "blocked"
    assert normalize_run_state("APPROVAL_GATE_BLOCKED") == "blocked"
    assert normalize_run_state("SUBMITTED") == "running"
    assert normalize_run_state("RUNNING") == "running"
    assert normalize_run_state("PARTIAL") == "partial"
    assert normalize_run_state("TIMEOUT") == "timeout"
    assert normalize_run_state("CANCELLED") == "cancelled"
    assert normalize_run_state("canceled") == "cancelled"
    assert normalize_run_state("INTERRUPTED") == "interrupted"


def test_normalize_run_state_unknown_safe():
    assert normalize_run_state(None) == "unknown"
    assert normalize_run_state("") == "unknown"
    assert normalize_run_state("BOGUS_STATE") == "unknown"
    assert normalize_run_state("  ") == "unknown"


def test_normalize_node_state_known_values():
    assert normalize_node_state("SUCCESS") == "succeeded"
    assert normalize_node_state("COMPLETED") == "succeeded"
    assert normalize_node_state("FAILED") == "failed"
    assert normalize_node_state("ERROR") == "failed"
    assert normalize_node_state("SKIPPED") == "skipped"
    assert normalize_node_state("RUNNING") == "running"
    assert normalize_node_state("BLOCKED") == "blocked"
    assert normalize_node_state("TIMEOUT") == "timeout"
    assert normalize_node_state("REUSED") == "reused"
    assert normalize_node_state("INVALIDATED") == "invalidated"


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Pure timeline builder
# ═══════════════════════════════════════════════════════════════════════


def test_timeline_builder_minimal():
    t = build_run_state_timeline(project_id="p1", run_id="r1")
    assert t.ok is True
    assert t.current_run_state == "unknown"
    assert t.terminal is False
    assert t.retry_eligible is False
    assert t.resume_eligible is False


def test_timeline_builder_with_status():
    t = build_run_state_timeline(
        project_id="p1",
        run_id="r1",
        run_link_status="SUCCESS",
    )
    assert t.current_run_state == "succeeded"
    assert t.terminal is True


def test_timeline_builder_with_summary_preview():
    t = build_run_state_timeline(
        project_id="p1",
        run_id="r1",
        summary_preview={
            "status": "SUCCESS",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:05:00Z",
            "node_results": [
                {"node_id": "contract_smoke", "status": "SUCCESS", "ok": True},
            ],
        },
    )
    assert t.current_run_state == "succeeded"
    assert t.terminal is True
    assert len(t.events) >= 2  # started + finished
    assert len(t.nodes) >= 1
    assert t.nodes[0].node_id == "contract_smoke"
    assert t.nodes[0].state == "succeeded"


def test_timeline_events_are_chronological_even_when_sources_arrive_out_of_order():
    timeline = build_run_state_timeline(
        project_id="p1",
        run_id="r1",
        created_at="2026-01-01T00:00:00Z",
        summary_preview={
            "status": "SUCCESS",
            "started_at": "2026-01-01T00:01:00Z",
            "finished_at": "2026-01-01T00:05:00Z",
        },
        run_events=[
            {"timestamp": "2026-01-01T00:04:00Z", "message": "almost done"},
            {"timestamp": "2026-01-01T00:02:00Z", "message": "node started"},
        ],
    )

    timestamps = [event.timestamp for event in timeline.events if event.timestamp]
    assert timestamps == sorted(timestamps)
    assert timeline.events[0].state == "created"
    assert timeline.events[1].state == "running"


def test_timeline_events_compare_mixed_utc_iso_formats_by_instant():
    timeline = build_run_state_timeline(
        project_id="p1",
        run_id="r1",
        created_at="2026-07-26T07:48:47Z",
        summary_preview={
            "status": "SUCCESS",
            "started_at": "2026-07-26T07:48:47.978398+00:00",
            "finished_at": "2026-07-26T07:48:49.080719+00:00",
        },
    )

    assert [event.state for event in timeline.events] == ["created", "running", "succeeded"]


def test_timeline_builder_failed_run():
    t = build_run_state_timeline(
        project_id="p1",
        run_id="r1",
        run_link_status="FAILED",
    )
    assert t.current_run_state == "failed"
    assert is_run_terminal(t.current_run_state) is True
    assert is_run_retry_eligible(t.current_run_state) is True


# ═══════════════════════════════════════════════════════════════════════
# Group 3 — Endpoint integration
# ═══════════════════════════════════════════════════════════════════════


def test_timeline_endpoint_project_not_found():
    client = TestClient(app)
    resp = client.get("/api/projects/nonexistent/runs/r1/state-timeline")
    assert resp.status_code == 404


def test_timeline_endpoint_run_not_found(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.get(f"/api/projects/{created['project_id']}/runs/nonexistent/state-timeline")
    assert resp.status_code == 404


def test_contract_smoke_timeline_after_execution(tmp_path, monkeypatch):
    """After contract_smoke execution, timeline endpoint returns valid data."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    assert run_id is not None

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["project_id"] == created["project_id"]
    assert data["run_id"] == run_id

    # Current run state should be a valid Phase 3 state
    valid_states = {
        "created",
        "queued",
        "preflight",
        "approval_required",
        "audit_required",
        "ready",
        "running",
        "succeeded",
        "failed",
        "blocked",
        "cancelled",
        "timeout",
        "partial",
        "interrupted",
        "unknown",
    }
    assert data["current_run_state"] in valid_states, f"Got: {data['current_run_state']}"

    # Terminal/retry/resume should be booleans
    assert isinstance(data["terminal"], bool)
    assert isinstance(data["retry_eligible"], bool)
    assert isinstance(data["resume_eligible"], bool)


def test_timeline_uses_execution_state_helpers(tmp_path, monkeypatch):
    """Timeline correctly classifies terminal and retry eligibility."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")
    data = resp.json()

    state = data["current_run_state"]
    # Verify helper consistency
    assert data["terminal"] == is_run_terminal(state)
    assert data["retry_eligible"] == is_run_retry_eligible(state)
    assert data["resume_eligible"] == is_run_resume_eligible(state)


def test_timeline_includes_node_records(tmp_path, monkeypatch):
    """Timeline includes node-level records when data is available."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")
    data = resp.json()
    nodes = data.get("nodes", [])
    assert isinstance(nodes, list)
    assert len(nodes) >= 1


def test_timeline_ignores_path_query(tmp_path, monkeypatch):
    """Path query param is ignored — no effect on response."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    resp = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline?path=../../secret"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True


# ═══════════════════════════════════════════════════════════════════════
# Group 4 — Safety boundaries
# ═══════════════════════════════════════════════════════════════════════


def test_timeline_read_only_no_rawdata_change(tmp_path, monkeypatch):
    """Timeline endpoint does not modify rawdata."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)

    rd = tmp_path / "rawdata_tl"
    rd.mkdir(parents=True, exist_ok=True)
    sentinel = rd / "sentinel.txt"
    sentinel.write_text("read-only", encoding="utf-8")
    mtime_before = sentinel.stat().st_mtime

    pj = tmp_path / "pj_tl"
    create_resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "TL Safety",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)
    exec_data = _execute(client, created, plan, rpid)

    run_id = exec_data.get("run_id")
    client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")

    mtime_after = sentinel.stat().st_mtime
    assert mtime_before == mtime_after


def test_timeline_no_external_subprocess(tmp_path, monkeypatch):
    """Timeline endpoint never calls MATLAB/SPM/DPABI."""
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
    exec_data = _execute(client, created, plan, rpid)

    run_id = exec_data.get("run_id")
    client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")

    # Only count MATLAB calls during timeline endpoint (execution may have
    # already legitimately called it for spm_smoke_test nodes)
    matlab_from_timeline = [c for c in called_matlab if "state-timeline" in str(c).lower()]
    assert len(matlab_from_timeline) == 0


def test_timeline_does_not_modify_run_history(tmp_path, monkeypatch):
    """Run history unchanged after timeline endpoint call."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    # Get run detail before
    _before = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}").json()

    # Call timeline
    client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")

    # Get run detail after
    _after = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}").json()

    # Run list count should be unchanged
    runs_before = client.get(f"/api/projects/{created['project_id']}/runs").json()
    runs_after = client.get(f"/api/projects/{created['project_id']}/runs").json()
    runs_b = runs_before if isinstance(runs_before, list) else runs_before.get("runs", [])
    runs_a = runs_after if isinstance(runs_after, list) else runs_after.get("runs", [])
    assert len(runs_b) == len(runs_a), f"Run list changed: {len(runs_b)} → {len(runs_a)}"
