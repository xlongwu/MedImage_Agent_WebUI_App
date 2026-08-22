from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.runtime.pipeline_executor import run_pipeline as real_run_pipeline
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.goal_contract_helpers import reviewed_goal_candidate


def _rawdata_snapshot(rawdata_dir: Path) -> dict[str, str]:
    return {
        str(path.relative_to(rawdata_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(rawdata_dir.rglob("*"))
        if path.is_file()
    }


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
        execute_reviewed_routes,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _safe_reviewed_plan(created: dict) -> dict:
    context = project_context.load_project_context(
        created["project_id"],
        created["project_config_path"],
    )
    return project_context.apply_project_context_to_plan(
        {
            "pipeline_id": "real_project_safe_inspection",
            "nodes": [
                {
                    "id": "data_inspection",
                    "backend": "python",
                    "depends_on": [],
                    "params": {"read_nifti_metadata": False},
                },
            ],
        },
        context,
    )


def _execute_body(
    created: dict,
    plan: dict,
    reviewed: dict,
    *,
    approved: bool = True,
    confirm_execution: bool = True,
) -> dict:
    return {
        "plan": plan,
        "approval": {
            "approved": approved,
            "approved_by": "safe-smoke-test",
            "approved_nodes": ["*"] if approved else [],
            "rejected_nodes": [],
            "approval_summary_hash": reviewed["payload"]["approval_envelope"][
                "summary_hash"
            ],
        },
        "project_id": created["project_id"],
        "reviewed_plan_id": reviewed["reviewed_plan_id"],
        "project_config_path": created["project_config_path"],
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": confirm_execution,
        "actor": "safe-smoke-test",
    }


@pytest.fixture
def real_project_smoke(tmp_path: Path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    rawdata_dir = Path("examples/synthetic_bids/rawdata").resolve()
    rawdata_before = _rawdata_snapshot(rawdata_dir)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Real Project Safe Smoke",
            "rawdata_dir": str(rawdata_dir),
            "project_dir": str(tmp_path / "real_project"),
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()

    detail = client.get(f"/api/projects/{created['project_id']}")
    assert detail.status_code == 200, detail.text
    metadata = detail.json()["metadata"]
    assert metadata["project_config_path"] == created["project_config_path"]
    assert metadata["dataset_index_path"] == created["dataset_index_path"]

    plan = _safe_reviewed_plan(created)
    goal = "Inspect the selected real project safely"
    saved = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True},
            "goal": goal,
            "provider": "deterministic-test",
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "safe-smoke-test",
        },
    )
    assert saved.status_code == 200, saved.text
    reviewed = saved.json()["reviewed_plan"]
    assert Path(reviewed["plan_path"]).is_file()

    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    monkeypatch.setattr(
        execute_reviewed_routes.pipeline_writer,
        "REVIEWED_PIPELINE_DIR",
        tmp_path / "pipelines",
    )

    return {
        "client": client,
        "store": store,
        "created": created,
        "plan": plan,
        "reviewed": reviewed,
        "rawdata_dir": rawdata_dir,
        "rawdata_before": rawdata_before,
    }


def test_real_project_safe_reviewed_execute_uses_real_executor(
    real_project_smoke,
    monkeypatch,
):
    client = real_project_smoke["client"]
    store = real_project_smoke["store"]
    created = real_project_smoke["created"]
    plan = real_project_smoke["plan"]
    reviewed = real_project_smoke["reviewed"]
    run_links_seen_before_executor: list[str] = []

    def observed_real_executor(
        *, project_config_path: str, pipeline_path: str, execution_context
    ) -> dict:
        pipeline = yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8"))
        run_id = pipeline["execution"]["run_id"]
        links = store.list_run_links(created["project_id"])
        matching = [link for link in links if link.run_id == run_id]
        assert len(matching) == 1
        assert matching[0].status == "RUNNING"
        run_links_seen_before_executor.append(matching[0].run_link_id)
        return real_run_pipeline(
            project_config_path=project_config_path,
            pipeline_path=pipeline_path,
            execution_context=execution_context,
        )

    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        observed_real_executor,
    )
    body = _execute_body(
        created,
        plan,
        reviewed,
    )
    first = client.post("/api/plans/execute-reviewed", json=body)
    second = client.post("/api/plans/execute-reviewed", json=body)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["status"] == "EXECUTION_SUBMITTED"
    assert first_payload["reviewed_plan_id"] == reviewed["reviewed_plan_id"]
    assert first_payload["executor_result"]["status"] == "SUCCESS"
    assert Path(first_payload["pipeline_path"]).is_file()
    assert Path(first_payload["summary_path"]).is_file()
    assert second_payload["status"] == "AGENT_LIFECYCLE_ID_REQUIRED"
    assert second_payload["run_id"] is None

    pipeline = yaml.safe_load(
        Path(first_payload["pipeline_path"]).read_text(encoding="utf-8")
    )
    assert [node["id"] for node in pipeline["nodes"]] == ["data_inspection"]
    assert pipeline["nodes"][0]["backend"] == "python"
    assert (
        Path(pipeline["nodes"][0]["params"]["rawdata_dir"]).resolve()
        == real_project_smoke["rawdata_dir"]
    )
    assert (
        Path(pipeline["nodes"][0]["params"]["output_dir"]).resolve()
        == Path(created["dataset_index_path"]).parent.resolve()
    )

    summary = json.loads(Path(first_payload["summary_path"]).read_text(encoding="utf-8"))
    assert summary["run_id"] == first_payload["run_id"]
    assert summary["status"] == "SUCCESS"
    assert len(run_links_seen_before_executor) == 1

    plans = client.get(f"/api/projects/{created['project_id']}/plans")
    plan_detail = client.get(
        f"/api/projects/{created['project_id']}/plans/{reviewed['reviewed_plan_id']}"
    )
    runs = client.get(f"/api/projects/{created['project_id']}/runs")
    assert plans.status_code == plan_detail.status_code == runs.status_code == 200
    assert len(plans.json()["reviewed_plans"]) == 1
    assert plan_detail.json()["reviewed_plan"]["reviewed_plan_id"] == reviewed["reviewed_plan_id"]
    assert {run["run_id"] for run in runs.json()["runs"]} == {first_payload["run_id"]}

    run_detail = client.get(
        f"/api/projects/{created['project_id']}/runs/{first_payload['run_id']}"
    )
    assert run_detail.status_code == 200
    run_link = run_detail.json()["run_link"]
    assert run_link["status"] == "SUCCESS"
    assert run_link["reviewed_plan_id"] == reviewed["reviewed_plan_id"]
    assert Path(run_link["summary_path"]).is_file()

    assert (
        _rawdata_snapshot(real_project_smoke["rawdata_dir"]) == real_project_smoke["rawdata_before"]
    )


def test_real_project_safe_reviewed_execute_still_requires_confirmation(
    real_project_smoke,
    monkeypatch,
):
    executor_called = False

    def fail_if_called(**kwargs):
        nonlocal executor_called
        executor_called = True
        raise AssertionError("executor must not run without approval")

    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        fail_if_called,
    )
    response = real_project_smoke["client"].post(
        "/api/plans/execute-reviewed",
        json=_execute_body(
            real_project_smoke["created"],
            real_project_smoke["plan"],
            real_project_smoke["reviewed"],
            confirm_execution=False,
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CONFIRMATION_REQUIRED"
    assert response.json()["execution"]["executor_called"] is False
    assert executor_called is False
    assert (
        real_project_smoke["store"].list_run_links(real_project_smoke["created"]["project_id"])
        == []
    )
    assert (
        _rawdata_snapshot(real_project_smoke["rawdata_dir"]) == real_project_smoke["rawdata_before"]
    )
