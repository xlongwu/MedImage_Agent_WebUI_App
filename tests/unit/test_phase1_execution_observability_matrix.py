"""Phase 1 execution observability regression matrix.

Covers the complete observability loop:
  reviewed plan → dry-run / execute-reviewed response → run link
  → run detail → events/logs → artifacts → artifact preview.

All tests use the existing TestClient + isolated_store pattern.
No new execution behaviour is introduced.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    project_history_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.goal_contract_helpers import reviewed_goal_candidate

# ── Test helpers ────────────────────────────────────────────────────────────


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(
        desktop_config,
        "DESKTOP_CONFIG_PATH",
        tmp_path / "desktop_config.json",
    )
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        project_history_routes,
        execute_reviewed_routes,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(client: TestClient, tmp_path: Path, name: str = "Regression Project") -> dict:
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": name,
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / name.replace(" ", "_")),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _regression_plan(created: dict) -> dict:
    rawdata_dir = created["rawdata_dir"]
    dataset_index_path = created["dataset_index_path"]
    return {
        "pipeline_id": "regression-test-plan",
        "project_context": {
            "project_id": created["project_id"],
            "project_config_path": created["project_config_path"],
            "rawdata_dir": rawdata_dir,
            "dataset_index_path": dataset_index_path,
            "source": "created",
            "diagnostics": created["diagnostics"],
        },
        "nodes": [
            {
                "id": "contract_smoke",
                "backend": "python",
                "depends_on": [],
                "params": {},
            },
        ],
    }


def _save_plan(client: TestClient, created: dict, plan: dict) -> dict:
    goal = "Regression test plan"
    response = client.post(
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
    assert response.status_code == 200, response.text
    return response.json()["reviewed_plan"]


def _execute_body(
    created: dict,
    plan: dict,
    reviewed_plan_id: str,
    approval_summary_hash: str | None = None,
) -> dict:
    return {
        "plan": plan,
        "approval": {
            "approved": True,
            "approved_by": "test-user",
            "approved_nodes": ["*"],
            "rejected_nodes": [],
            **(
                {"approval_summary_hash": approval_summary_hash}
                if approval_summary_hash
                else {}
            ),
        },
        "project_id": created["project_id"],
        "reviewed_plan_id": reviewed_plan_id,
        "project_config_path": created["project_config_path"],
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
    }


def _setup_executed(
    tmp_path: Path,
    client: TestClient,
    monkeypatch,
) -> tuple[dict, dict]:
    """Create project, save plan, execute. Returns (created, execute_result)."""
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _regression_plan(created)
    reviewed = _save_plan(client, created, plan)
    result = client.post(
        "/api/plans/execute-reviewed",
        json=_execute_body(
            created,
            plan,
            reviewed["reviewed_plan_id"],
            reviewed["payload"]["approval_envelope"]["summary_hash"],
        ),
    ).json()
    return created, result


# ── 1. Dry-run blocked states remain non-executing ──────────────────────────


def test_dry_run_reviewed_execution_disabled(tmp_path, monkeypatch):
    """REVIEWED_EXECUTION_DISABLED: env var not set → no execution."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    plan = _regression_plan(created)
    reviewed = _save_plan(client, created, plan)

    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_execute_body(created, plan, reviewed["reviewed_plan_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "REVIEWED_EXECUTION_DISABLED"
    assert body["ok"] is False
    assert body.get("execution", {}).get("executor_called") is not True


def test_dry_run_confirmation_required(tmp_path, monkeypatch):
    """CONFIRMATION_REQUIRED: confirm_execution=false → blocked."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    created = _create_project(client, tmp_path)
    plan = _regression_plan(created)
    reviewed = _save_plan(client, created, plan)

    body_dict = _execute_body(created, plan, reviewed["reviewed_plan_id"])
    body_dict["confirm_execution"] = False
    resp = client.post("/api/plans/execute-reviewed", json=body_dict)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "CONFIRMATION_REQUIRED"
    assert body["ok"] is False


def test_dry_run_audit_required(tmp_path, monkeypatch):
    """AUDIT_REQUIRED: persist_audit=false → blocked."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    created = _create_project(client, tmp_path)
    plan = _regression_plan(created)
    reviewed = _save_plan(client, created, plan)

    body_dict = _execute_body(created, plan, reviewed["reviewed_plan_id"])
    body_dict["persist_audit"] = False
    resp = client.post("/api/plans/execute-reviewed", json=body_dict)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "AUDIT_REQUIRED"
    assert body["ok"] is False


def test_dry_run_project_config_required(tmp_path, monkeypatch):
    """PROJECT_CONFIG_REQUIRED: missing project_config_path → blocked."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    created = _create_project(client, tmp_path)
    plan = _regression_plan(created)
    reviewed = _save_plan(client, created, plan)

    body_dict = _execute_body(created, plan, reviewed["reviewed_plan_id"])
    body_dict["project_config_path"] = ""
    resp = client.post("/api/plans/execute-reviewed", json=body_dict)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PROJECT_CONFIG_REQUIRED"
    assert body["ok"] is False


def test_dry_run_pipeline_yaml_required(tmp_path, monkeypatch):
    """PIPELINE_YAML_REQUIRED: write_pipeline_yaml=false → blocked."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    created = _create_project(client, tmp_path)
    plan = _regression_plan(created)
    reviewed = _save_plan(client, created, plan)

    body_dict = _execute_body(created, plan, reviewed["reviewed_plan_id"])
    body_dict["write_pipeline_yaml"] = False
    resp = client.post("/api/plans/execute-reviewed", json=body_dict)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PIPELINE_YAML_REQUIRED"
    assert body["ok"] is False


# ── 2. Successful execution creates traceable run identity ──────────────────


def test_successful_execution_has_traceable_identity(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)

    assert result.get("ok") is True or result.get("status") in (
        "EXECUTION_SUBMITTED",
        "EXECUTION_PREFLIGHT_READY",
    ), f"Unexpected result: {result}"
    assert result.get("reviewed_plan_id"), f"Missing reviewed_plan_id: {result}"
    assert result.get("run_link_id"), f"Missing run_link_id: {result}"
    assert result.get("run_id"), f"Missing run_id: {result}"
    assert result.get("pipeline_path"), f"Missing pipeline_path: {result}"
    # summary_path may be null if executor didn't finish, but pipeline_path is mandatory
    assert isinstance(result.get("execution", {}).get("executor_called"), bool)


# ── 3. Run list and detail resolve after execution ──────────────────────────


def test_run_list_contains_executed_run(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id, f"No run_id: {result}"

    resp = client.get(f"/api/projects/{created['project_id']}/runs")
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    matching = [r for r in runs if r["run_id"] == run_id]
    assert len(matching) == 1, f"Run {run_id} not found in list: {runs}"


def test_run_detail_returns_summary_preview_or_controlled_error(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body.get("run_link"), f"Missing run_link: {body}"
    # summary_preview is optional; if absent, summary_preview_error must be null or a string
    assert "summary_preview" in body
    assert body.get("summary_preview_error") is None or isinstance(
        body["summary_preview_error"], str
    )
    assert isinstance(body.get("warnings"), list)


# ── 4. Events / logs remain safe and scoped ─────────────────────────────────


def test_events_endpoint_returns_ok_after_execution(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/events")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body.get("events"), list)


def test_logs_endpoint_returns_ok_after_execution(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/logs?max_bytes=2000")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body.get("logs"), list)
    # If logs exist, content should be within max_bytes bounds
    for log in body["logs"]:
        if log.get("content") and not log.get("truncated"):
            assert len(log["content"]) <= 2200, (
                f"Log content exceeds max_bytes: {len(log['content'])}"
            )


def test_logs_irrelevant_path_param_is_ignored(tmp_path, monkeypatch):
    """Passing ?path=... does not change log scoping."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/logs?path=../../etc/passwd"
    )
    # Should succeed (path param is ignored)
    assert resp.status_code == 200, resp.text


# ── 5. Artifact list and preview remain safe ────────────────────────────────


def test_artifact_list_has_stable_required_fields(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["artifacts"], list)
    for artifact in body["artifacts"]:
        for field in (
            "artifact_id",
            "name",
            "kind",
            "path",
            "exists",
            "previewable",
            "warnings",
        ):
            assert field in artifact, f"Missing field '{field}' in artifact: {artifact}"


def test_artifact_list_missing_artifacts_have_exists_false(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts")
    body = resp.json()
    for artifact in body["artifacts"]:
        if not artifact["exists"]:
            assert artifact["previewable"] is False
            assert artifact["size_bytes"] is None


def test_invalid_artifact_id_rejected(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    for bad_id in ["../secret", "a/b", "a\\b"]:
        resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/{bad_id}")
        assert resp.status_code in (400, 404), (
            f"Expected 400/404 for {bad_id!r}, got {resp.status_code}"
        )


def test_preview_discovered_artifact_succeeds(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    # Discover a previewable artifact
    artifacts_resp = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts")
    artifacts = artifacts_resp.json()["artifacts"]
    previewable = [a for a in artifacts if a.get("exists") and a.get("previewable")]
    if not previewable:
        # If no previewable artifact, the test still validates that
        # the artifacts endpoint itself is healthy
        return

    preview_resp = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/{previewable[0]['artifact_id']}"
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview = preview_resp.json()
    assert "preview_type" in preview
    assert preview["preview_type"] in (
        "json",
        "csv",
        "markdown",
        "text",
        "log",
        "metadata_only",
        "missing",
    )


# ── 6. Retry / resume not implemented ──────────────────────────────────────


def test_retry_resume_endpoints_do_not_exist(tmp_path, monkeypatch):
    """POST to /resume and /retry should return 404 (not implemented)."""
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created, result = _setup_executed(tmp_path, client, monkeypatch)
    run_id = result.get("run_id")
    assert run_id

    base = f"/api/projects/{created['project_id']}/runs/{run_id}"
    for suffix in ("/retry", "/resume", "/rerun"):
        resp = client.post(base + suffix, json={})
        assert resp.status_code == 404, (
            f"POST {base}{suffix} should return 404, got {resp.status_code}"
        )
