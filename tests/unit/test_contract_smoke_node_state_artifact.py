"""contract_smoke node-state artifact normalization tests.

Validates that contract_smoke emits a normalized Phase 3 node-state
JSON artifact and that the timeline consumes it correctly.
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
    is_node_retry_eligible,
    is_node_reuse_eligible,
    is_node_terminal,
)
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
            "project_name": "NodeState Test",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_plan(created: dict) -> dict:
    return {
        "pipeline_id": "test_nodestate",
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


def _save_plan(client: TestClient, created: dict, plan: dict) -> dict:
    goal = "NodeState test"
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
    return resp.json()["reviewed_plan"]


def _execute(client: TestClient, created: dict, plan: dict, reviewed: dict) -> dict:
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {
                "approved": True,
                "approved_by": "test",
                "approved_nodes": ["*"],
                "rejected_nodes": [],
                "approval_summary_hash": reviewed["payload"]["approval_envelope"][
                    "summary_hash"
                ],
            },
            "project_id": created["project_id"],
            "reviewed_plan_id": reviewed["reviewed_plan_id"],
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


def _get_artifacts(client, project_id, run_id) -> list:
    resp = client.get(f"/api/projects/{project_id}/runs/{run_id}/artifacts")
    assert resp.status_code == 200
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("artifacts", [])


# ═══════════════════════════════════════════════════════════════════════
# Group 1 — Node-state artifact exists and is valid
# ═══════════════════════════════════════════════════════════════════════


def test_contract_smoke_writes_node_state_artifact(tmp_path, monkeypatch):
    """contract_smoke execution produces contract_smoke_node_state.json."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)
    run_id = exec_data["run_id"]

    artifacts = _get_artifacts(client, created["project_id"], run_id)
    names = [a.get("name", "") for a in artifacts]
    assert "contract_smoke_node_state.json" in names, f"Node-state not found in: {names}"


def test_node_state_artifact_schema_fields(tmp_path, monkeypatch):
    """Node-state artifact has correct schema_version and field values."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)
    run_id = exec_data["run_id"]

    artifacts = _get_artifacts(client, created["project_id"], run_id)
    ns_art = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_node_state.json"),
        None,
    )
    assert ns_art is not None
    payload = json.loads(Path(ns_art["path"]).read_text(encoding="utf-8"))

    assert payload["schema_version"] == "phase3-node-state-v1"
    assert payload["node_id"] == "contract_smoke"
    assert payload["state"] == "succeeded"
    assert payload["terminal"] is True
    assert payload["retry_eligible"] is False
    assert payload["reuse_eligible"] is True


def test_node_state_uses_execution_state_helpers(tmp_path, monkeypatch):
    """Artifact booleans match execution_state.py helpers."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)
    run_id = exec_data["run_id"]

    artifacts = _get_artifacts(client, created["project_id"], run_id)
    ns_art = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_node_state.json"),
        None,
    )
    payload = json.loads(Path(ns_art["path"]).read_text(encoding="utf-8"))

    state = payload["state"]
    assert payload["terminal"] == is_node_terminal(state)
    assert payload["retry_eligible"] == is_node_retry_eligible(state)
    assert payload["reuse_eligible"] == is_node_reuse_eligible(state)


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Output manifest integration
# ═══════════════════════════════════════════════════════════════════════


def test_output_manifest_includes_node_state_artifact(tmp_path, monkeypatch):
    """Output manifest has an item for the node-state JSON."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)
    run_id = exec_data["run_id"]

    artifacts = _get_artifacts(client, created["project_id"], run_id)
    manifest_art = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_output_manifest.json"),
        None,
    )
    assert manifest_art is not None
    manifest = json.loads(Path(manifest_art["path"]).read_text(encoding="utf-8"))

    node_state_items = [i for i in manifest.get("items", []) if i.get("kind") == "node_state_json"]
    assert len(node_state_items) >= 1, "No node_state_json item in manifest items"


def test_node_state_artifact_previewable(tmp_path, monkeypatch):
    """Node-state artifact is previewable via artifact detail API."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)
    run_id = exec_data["run_id"]

    artifacts = _get_artifacts(client, created["project_id"], run_id)
    ns_art = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_node_state.json"),
        None,
    )
    assert ns_art is not None
    assert ns_art.get("previewable") is True
    assert ns_art.get("kind") == "json"

    aid = ns_art["artifact_id"]
    preview_resp = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/{aid}"
    )
    assert preview_resp.status_code == 200, preview_resp.text


# ═══════════════════════════════════════════════════════════════════════
# Group 3 — Timeline integration
# ═══════════════════════════════════════════════════════════════════════


def test_timeline_uses_node_state_artifact(tmp_path, monkeypatch):
    """Timeline includes contract_smoke node from normalized artifact."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)
    run_id = exec_data["run_id"]

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")
    assert resp.status_code == 200
    data = resp.json()

    nodes = data.get("nodes", [])
    cs_node = next((n for n in nodes if n.get("node_id") == "contract_smoke"), None)
    assert cs_node is not None, f"contract_smoke not in nodes: {nodes}"
    assert cs_node["state"] == "succeeded"
    assert cs_node["terminal"] is True
    assert cs_node["reuse_eligible"] is True


def test_malformed_node_state_artifact_does_not_crash_timeline(
    tmp_path,
    monkeypatch,
):
    """Corrupt node-state JSON produces warnings, not 500."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)
    run_id = exec_data["run_id"]

    # Corrupt the node-state file
    artifacts = _get_artifacts(client, created["project_id"], run_id)
    ns_art = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_node_state.json"),
        None,
    )
    if ns_art:
        Path(ns_art["path"]).write_text("not valid json {{{", encoding="utf-8")

    # Timeline should still return 200
    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/state-timeline")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True


# ═══════════════════════════════════════════════════════════════════════
# Group 4 — Safety boundaries
# ═══════════════════════════════════════════════════════════════════════


def test_node_state_does_not_modify_rawdata(tmp_path, monkeypatch):
    """Node-state artifact creation does not modify rawdata."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)

    rd = tmp_path / "rawdata_ns"
    rd.mkdir(parents=True, exist_ok=True)
    sentinel = rd / "sentinel.txt"
    sentinel.write_text("read-only", encoding="utf-8")
    mtime_before = sentinel.stat().st_mtime

    pj = tmp_path / "pj_ns"
    create_resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "NS Safety",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)
    _execute(client, created, plan, rpid)

    mtime_after = sentinel.stat().st_mtime
    assert mtime_before == mtime_after


def test_no_external_subprocess_called(tmp_path, monkeypatch):
    """Node-state artifact never calls MATLAB/SPM/DPABI."""
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

    assert len(called_matlab) == 0, f"MATLAB called: {called_matlab}"


def test_spm_realign_still_not_executable():
    """spm_realign_subject remains not executable."""
    from src.backend.app.runtime.tool_catalog import get_tool_catalog_item

    item = get_tool_catalog_item("spm_realign_subject")
    assert item.manual_required is True
    assert item.risk_level == "high"
    assert "not-executable" in item.tags
