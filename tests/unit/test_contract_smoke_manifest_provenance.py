"""contract_smoke output manifest and provenance integration tests.

Validates that contract_smoke execution produces standardized
OutputManifest and ExecutionProvenance artifacts discoverable through
the existing artifact discovery and preview APIs.
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
from src.backend.app.schemas.execution_manifest import (
    ExecutionProvenance,
    OutputManifest,
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
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
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
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit_records")
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
        '{"Name": "test", "BIDSVersion": "1.8.0"}', encoding="utf-8"
    )
    pj = tmp_path / "project"
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "MVP Manifest Test",
            "rawdata_dir": str(rd),
            "project_dir": str(pj),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_plan(created: dict) -> dict:
    return {
        "pipeline_id": "test_manifest",
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
    goal = "Manifest integration test"
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
    """Create project, plan, save, execute. Returns (created, exec_data)."""
    created = _create_project(client, tmp_path)
    plan = _make_plan(created)
    rpid = _save_plan(client, created, plan)
    data = _execute(client, created, plan, rpid)
    return created, data


def _get_artifacts(client, project_id, run_id) -> list:
    """Fetch artifact list from the API, handling dict wrapper."""
    resp = client.get(f"/api/projects/{project_id}/runs/{run_id}/artifacts")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("artifacts", [])
    return []


# ═══════════════════════════════════════════════════════════════════════
# Group 1 — Output manifest artifact
# ═══════════════════════════════════════════════════════════════════════


def test_contract_smoke_writes_output_manifest(tmp_path, monkeypatch):
    """contract_smoke execution produces output_manifest.json artifact."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    # Discover artifacts via API
    run_id = exec_data.get("run_id")
    assert run_id is not None, f"No run_id in: {json.dumps(exec_data, indent=2)}"

    artifacts = _get_artifacts(client, created["project_id"], run_id)

    # Find manifest
    manifest_names = [a.get("name", "") for a in artifacts]
    assert "contract_smoke_output_manifest.json" in manifest_names, (
        f"Manifest not found in artifacts: {manifest_names}"
    )


def test_contract_smoke_output_manifest_schema_valid(tmp_path, monkeypatch):
    """Output manifest parses as valid OutputManifest model."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    artifacts = _get_artifacts(client, created["project_id"], run_id)

    # Find and load manifest
    manifest_artifact = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_output_manifest.json"),
        None,
    )
    assert manifest_artifact is not None, "Manifest artifact not found"
    assert manifest_artifact.get("exists") is True

    manifest_path = Path(manifest_artifact["path"])
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    m = OutputManifest(**raw)

    assert isinstance(m.project_id, str) and len(m.project_id) > 0
    assert m.run_id == run_id
    assert m.node_id == "contract_smoke"
    assert m.missing_required_count == 0
    assert m.verified_count >= 2  # at least report + manifest itself
    assert len(m.items) >= 3  # report, log, manifest, provenance


def test_manifest_items_reference_existing_files(tmp_path, monkeypatch):
    """Every required item with verified=True exists on disk."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    artifacts = _get_artifacts(client, created["project_id"], run_id)

    manifest_artifact = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_output_manifest.json"),
        None,
    )
    assert manifest_artifact is not None
    manifest_path = Path(manifest_artifact["path"])
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    m = OutputManifest(**raw)

    for item in m.items:
        if item.required and item.verified:
            p = Path(item.path)
            assert p.exists(), f"Required verified item missing: {p}"
            assert p.stat().st_size > 0, f"Required verified item empty: {p}"


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Execution provenance artifact
# ═══════════════════════════════════════════════════════════════════════


def test_contract_smoke_writes_execution_provenance(tmp_path, monkeypatch):
    """contract_smoke execution produces provenance JSON artifact."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    artifacts = _get_artifacts(client, created["project_id"], run_id)

    provenance_names = [a.get("name", "") for a in artifacts]
    assert "contract_smoke_execution_provenance.json" in provenance_names, (
        f"Provenance not found: {provenance_names}"
    )


def test_contract_smoke_provenance_schema_valid(tmp_path, monkeypatch):
    """Provenance parses as valid ExecutionProvenance model."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    artifacts = _get_artifacts(client, created["project_id"], run_id)

    prov_artifact = next(
        (a for a in artifacts if a.get("name") == "contract_smoke_execution_provenance.json"),
        None,
    )
    assert prov_artifact is not None, "Provenance artifact not found"
    assert prov_artifact.get("exists") is True

    prov_path = Path(prov_artifact["path"])
    raw = json.loads(prov_path.read_text(encoding="utf-8"))
    p = ExecutionProvenance(**raw)

    assert p.backend == "python"
    assert p.node_id == "contract_smoke"
    assert isinstance(p.project_id, str) and len(p.project_id) > 0
    assert p.run_id == run_id
    assert p.return_code == 0
    assert "shell_command" not in raw, "shell_command must not be in provenance"


# ═══════════════════════════════════════════════════════════════════════
# Group 3 — Artifact preview
# ═══════════════════════════════════════════════════════════════════════


def test_manifest_and_provenance_previewable(tmp_path, monkeypatch):
    """Manifest and provenance artifacts are JSON-previewable."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    run_id = exec_data.get("run_id")
    artifacts = _get_artifacts(client, created["project_id"], run_id)

    for name in ("contract_smoke_output_manifest.json", "contract_smoke_execution_provenance.json"):
        art = next((a for a in artifacts if a.get("name") == name), None)
        assert art is not None, f"{name} not found"
        assert art.get("previewable") is True, f"{name} should be previewable"
        assert art.get("kind") == "json"

        # Preview via API
        aid = art.get("artifact_id")
        # Artifact detail endpoint returns preview payload directly
        preview_resp = client.get(
            f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/{aid}"
        )
        assert preview_resp.status_code == 200, f"Preview failed for {name}: {preview_resp.text}"


# ═══════════════════════════════════════════════════════════════════════
# Group 4 — Safety boundaries
# ═══════════════════════════════════════════════════════════════════════


def test_manifest_does_not_modify_rawdata(tmp_path, monkeypatch):
    """contract_smoke with manifest does not modify rawdata."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)

    rd = tmp_path / "rawdata_man"
    rd.mkdir(parents=True, exist_ok=True)
    sentinel = rd / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    mtime_before = sentinel.stat().st_mtime

    pj = tmp_path / "project_man"
    create_resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "Manifest Safety",
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
    assert mtime_before == mtime_after, (
        f"Rawdata sentinel mtime changed: {mtime_before} → {mtime_after}"
    )


def test_manifest_no_external_subprocess(tmp_path, monkeypatch):
    """contract_smoke with manifest never calls MATLAB/SPM/DPABI."""
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


def test_spm_realign_still_not_executable():
    """spm_realign_subject remains not executable."""
    from src.backend.app.runtime.tool_catalog import get_tool_catalog_item

    item = get_tool_catalog_item("spm_realign_subject")
    assert item.manual_required is True
    assert item.risk_level == "high"
    assert "not-executable" in item.tags


# ═══════════════════════════════════════════════════════════════════════
# Group 5 — Run history still visible
# ═══════════════════════════════════════════════════════════════════════


def test_run_history_still_visible_with_manifest_artifacts(tmp_path, monkeypatch):
    """Run history and detail remain readable after manifest integration."""
    _isolated_store(tmp_path, monkeypatch)
    _enable_env(monkeypatch)
    client = TestClient(app)
    created, exec_data = _setup_executed(tmp_path, monkeypatch, client)

    # Run history
    hist_resp = client.get(f"/api/projects/{created['project_id']}/runs")
    assert hist_resp.status_code == 200, hist_resp.text
    runs_data = hist_resp.json()
    runs = runs_data if isinstance(runs_data, list) else runs_data.get("runs", [])
    assert len(runs) >= 1

    # Run detail
    run_id = exec_data.get("run_id")
    detail_resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}")
    assert detail_resp.status_code == 200, detail_resp.text
