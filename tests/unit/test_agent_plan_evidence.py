"""Saved plan-only evidence is readable without manufacturing a run."""
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.main import app
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.agent_trace_service import AgentTraceService
from tests.unit.test_agent_task_commands import AgentTaskCommandService
from tests.unit.test_project_agent_settings import _store
from tests.unit.test_agent_trace_service import _trace_store


def _saved_plan(tmp_path):
    store = _store(tmp_path)
    store.update_project_metadata("project-1", {"project_config_path": str(Path("examples/project_config_synthetic_smoke.yaml").resolve())})
    lifecycle = AgentTaskCommandService(store).create(
        project_id="project-1", goal="检查 BIDS 数据并准备预处理方案，不执行计算，不修改 rawdata。",
        command_id="plan-only", actor="test",
    )
    assert lifecycle.state == "SUCCEEDED"
    return store, lifecycle


def _counts(store):
    with store._connect() as conn:
        return {row["name"]: conn.execute(f'SELECT COUNT(*) FROM "{row["name"]}"').fetchone()[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_real_saved_plan_read_is_safe_scoped_and_without_side_effects(tmp_path, monkeypatch):
    from src.backend.app.services.approval_summary_service import ApprovalSummaryService
    def forbidden_approval(*args, **kwargs):
        raise AssertionError("Plan-only must not construct an execution approval.")
    monkeypatch.setattr(ApprovalSummaryService, "build", forbidden_approval)
    store, task = _saved_plan(tmp_path)
    plan = store.get_reviewed_plan(task.reviewed_plan_id)
    assert "approval_summary" not in plan.payload
    assert "approval_envelope" not in plan.payload
    before = _counts(store)
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        client = TestClient(app)
        path = f"/api/projects/project-1/agent/tasks/{task.lifecycle_id}/plan-evidence"
        response = client.get(path, params={"plan_hash": plan.plan_hash})
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["plan_hash"] == plan.plan_hash
        assert body["reviewed_plan_id"] == task.reviewed_plan_id
        assert body["nodes"] and body["revision_no"] == 1
        assert str(tmp_path) not in response.text
        assert "project_config_path" not in response.text and "rawdata_dir" not in response.text
        assert client.get(path.replace("project-1", "other")).status_code == 404
        stale = client.get(path, params={"plan_hash": "0" * 64})
        assert stale.status_code == 403 and stale.json()["error"]["code"] == "AGENT_PLAN_EVIDENCE_STALE"
        assert client.get(path.replace(task.lifecycle_id, "project%3A%2F%2Finvalid")).status_code == 404
        assert client.get(f"/api/projects/project-1/agent/tasks/{task.lifecycle_id}/trace").status_code == 200
    finally:
        app.dependency_overrides.pop(get_project_store, None)
    assert _counts(store) == before
    assert SQLiteDesktopStore(store.db_path).get_reviewed_plan(plan.reviewed_plan_id) == plan
    assert store.list_execution_tickets("project-1") == []
    assert store.list_run_links("project-1") == []


def test_trace_projection_does_not_copy_model_action_text(tmp_path):
    store, task = _trace_store(tmp_path)
    attempt = store.get_agent_harness_attempt(task.lifecycle_id)
    action = store.list_agent_harness_actions(attempt.attempt_id)[0]
    store.update_agent_harness_action(action.model_copy(update={
        "action_payload": {"kind": "draft_plan", "reason": "SECRET_PROMPT C:/private/research token=SECRET_CREDENTIAL"},
    }), expected_status="applied")
    bundle = AgentTraceService(store).get(project_id=task.project_id, lifecycle_id=task.lifecycle_id)
    body = bundle.model_dump_json()
    assert "SECRET_PROMPT" not in body and "SECRET_CREDENTIAL" not in body
    assert "action_payload" not in body
    assert bundle.entries[0].service_name == "AgentPlanningActionService"
    assert bundle.entries[0].rationale_code == "DRAFT_REVIEWED_PLAN"


def test_plan_evidence_missing_and_corrupt_payload_are_explicit_and_read_only(tmp_path):
    from src.backend.app.core.exceptions import SafetyError
    from src.backend.app.services.agent_plan_evidence_service import AgentPlanEvidenceService
    from src.backend.app.services.agent_orchestrator import AgentOrchestrator
    from src.backend.app.planner.audit_record import stable_hash
    import pytest

    store, task = _saved_plan(tmp_path)
    pending = AgentOrchestrator(store).create(project_id="project-1", command_id="new", actor="test", goal_text="new")
    service = AgentPlanEvidenceService(store)
    assert service.get(project_id="project-1", task_id=pending.lifecycle_id).missing_code == "AGENT_PLAN_NOT_SAVED"
    record = store.get_reviewed_plan(task.reviewed_plan_id)
    plan = {**record.payload["plan"], "tampered": True}
    corrupt = record.model_copy(update={"payload": {**record.payload, "plan": plan, "normalized_plan_hash": stable_hash(plan)}})
    with store._connect() as conn:
        conn.execute("UPDATE reviewed_plans SET payload=? WHERE reviewed_plan_id=?", (corrupt.model_dump_json(), record.reviewed_plan_id))
    before = _counts(store)
    with pytest.raises(SafetyError, match="AGENT_PLAN_EVIDENCE_CORRUPT"):
        service.get(project_id="project-1", task_id=task.lifecycle_id)
    assert _counts(store) == before
    with store._connect() as conn:
        conn.execute("DELETE FROM reviewed_plans WHERE reviewed_plan_id=?", (record.reviewed_plan_id,))
    assert service.get(project_id="project-1", task_id=task.lifecycle_id).missing_code == "AGENT_PLAN_RECORD_MISSING"
