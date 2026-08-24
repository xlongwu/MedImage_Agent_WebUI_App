from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import src.backend.app.services.agent_orchestrator as agent_orchestrator_module
from src.backend.app.api.dependencies import get_project_store
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.main import app
from src.backend.app.runtime.execution_gateway import (
    ExecutionGateway,
    current_allowlist_hash,
)
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.execution_environment_service import ExecutionEnvironmentService
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _store(tmp_path: Path) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "lifecycle.sqlite")
    for project_id in ("project-1", "project-2"):
        store.add_project(
            ProjectDetail(
                id=project_id,
                name=project_id,
                study_id=project_id,
                modality="rs-fMRI",
                created_date="test",
                subjects_count=0,
                current_pipeline_id="test",
                sequences=[],
                scans_count=0,
                total_size="0 B",
                current_model_id="none",
            ),
            health_status="Ready",
            rawdata_dir="",
        )
    return store


def _ticket(store: SQLiteDesktopStore, tmp_path: Path, *, retry_quota: int = 0):
    config = tmp_path / "project.yaml"
    pipeline = tmp_path / "pipeline.yaml"
    config.write_text("runtime: {}\n", encoding="utf-8")
    pipeline.write_text("nodes: []\n", encoding="utf-8")
    inputs = tmp_path / "inputs"
    outputs = tmp_path / "outputs"
    inputs.mkdir(exist_ok=True)
    outputs.mkdir(exist_ok=True)
    service = ExecutionTicketService(store)
    environment = ExecutionEnvironmentService(store).capture_for_plan(
        project_id="project-1",
        reviewed_plan=SimpleNamespace(
            payload={"plan": {"nodes": [{"id": "data_inspection", "backend": "python"}]}},
        ),
        write_roots=("project://derivatives",),
        readonly_roots=("project://rawdata",),
    )
    ticket = service.issue(
        project_id="project-1",
        reviewed_plan_id="reviewed-1",
        plan_hash="plan-hash",
        goal_contract_hash="goal-contract-hash",
        evaluation_policy_version="goal-evaluator-v1",
        approval_summary_hash="approval-1",
        execution_environment_snapshot_id=environment.snapshot_id,
        execution_environment_hash=environment.environment_hash,
        memory_context_hash=None,
        approved_actor="reviewer",
        approved_node_ids=["data_inspection"],
        approved_backend_ids=["python"],
        input_roots=[str(inputs)],
        output_roots=[str(outputs)],
        project_config_path=str(config),
        pipeline_path=str(pipeline),
        allowlist_hash=current_allowlist_hash(),
        normalized_params_hash="normalized-params-hash",
        contract_versions={"data_inspection": "1.0.0"},
        audit_id="audit-1",
        max_retry_count=retry_quota,
    )
    return service, ticket


def _prepared(tmp_path: Path, *, retry_quota: int = 0):
    store = _store(tmp_path)
    service, ticket = _ticket(store, tmp_path, retry_quota=retry_quota)
    orchestrator = AgentOrchestrator(store)
    lifecycle = orchestrator.prepare_reviewed_execution(
        project_id="project-1",
        reviewed_plan_id=ticket.reviewed_plan_id,
        execution_ticket_id=ticket.execution_ticket_id,
        audit_id=ticket.audit_id,
        actor="reviewer",
    )
    return store, service, ticket, orchestrator, lifecycle


def _dispatch(service, ticket, orchestrator, lifecycle, *, status="SUCCESS"):
    def fake_executor(**kwargs):
        return {"status": status, "run_id": "run-1"}

    return orchestrator.dispatch_execution(
        lifecycle=lifecycle,
        actor="reviewer",
        dispatch=lambda: ExecutionGateway(service).dispatch(
            execution_ticket_id=ticket.execution_ticket_id,
            project_id=ticket.project_id,
            reviewed_plan_id=ticket.reviewed_plan_id,
            plan_hash=ticket.plan_hash,
            approval_summary_hash=ticket.approval_summary_hash,
            memory_context_hash=ticket.memory_context_hash,
            scope_hash=ticket.scope_hash,
            normalized_params_hash=ticket.normalized_params_hash,
            contract_versions=ticket.contract_versions,
            project_config_path=ticket.project_config_path,
            pipeline_path=ticket.pipeline_path,
            command_id="agent-lifecycle-dispatch",
            run_id="run-1",
            executor=fake_executor,
        ),
    )


def test_state_chain_persists_and_reloads_with_event_ledger(tmp_path, monkeypatch):
    fixed_time = datetime(2099, 1, 1, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_time if tz else fixed_time.replace(tzinfo=None)

    monkeypatch.setattr(agent_orchestrator_module, "datetime", FrozenDateTime)
    store, service, ticket, orchestrator, lifecycle = _prepared(tmp_path)
    _, _, lifecycle = _dispatch(service, ticket, orchestrator, lifecycle)
    assert lifecycle.state == "RUNNING"
    events = orchestrator.events(project_id="project-1", lifecycle_id=lifecycle.lifecycle_id)
    assert [event.to_state for event in events] == [
        "CREATED",
        "CONTEXT_READY",
        "PLAN_DRAFTED",
        "PLAN_VALIDATED",
        "WAITING_FOR_APPROVAL",
        "APPROVED",
        "EXECUTION_READY",
        "RUNNING",
    ]

    reloaded = SQLiteDesktopStore(store.db_path)
    recovered = AgentOrchestrator(reloaded).get(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id
    )
    assert recovered == lifecycle


def test_illegal_replayed_missing_ticket_and_cross_project_commands_are_rejected(tmp_path):
    store = _store(tmp_path)
    orchestrator = AgentOrchestrator(store)
    lifecycle = orchestrator.create(project_id="project-1", command_id="create-1", actor="user")
    with pytest.raises(SafetyError, match="LIFECYCLE_TRANSITION_INVALID"):
        orchestrator.transition(
            project_id="project-1",
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="RUNNING",
            command_id="illegal",
            actor="user",
            source_command="start",
        )
    lifecycle = orchestrator.transition(
        project_id="project-1",
        lifecycle_id=lifecycle.lifecycle_id,
        to_state="CONTEXT_READY",
        command_id="shared-command",
        actor="user",
        source_command="context_ready",
    )
    with pytest.raises(SafetyError, match="LIFECYCLE_COMMAND_REPLAYED"):
        orchestrator.transition(
            project_id="project-1",
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="PLAN_DRAFTED",
            command_id="shared-command",
            actor="user",
            source_command="plan_drafted",
        )
    with pytest.raises(SafetyError, match="LIFECYCLE_NOT_FOUND"):
        orchestrator.get(project_id="project-2", lifecycle_id=lifecycle.lifecycle_id)

    lifecycle2 = orchestrator.create(project_id="project-1", command_id="create-2", actor="user")
    for index, state in enumerate(
        ("CONTEXT_READY", "PLAN_DRAFTED", "PLAN_VALIDATED", "WAITING_FOR_APPROVAL", "APPROVED")
    ):
        lifecycle2 = orchestrator.transition(
            project_id="project-1",
            lifecycle_id=lifecycle2.lifecycle_id,
            to_state=state,
            command_id=f"step-{index}",
            actor="user",
            source_command="test",
            updates={"reviewed_plan_id": "reviewed-1"} if state != "CONTEXT_READY" else None,
        )
    with pytest.raises(SafetyError, match="LIFECYCLE_TICKET_REQUIRED"):
        orchestrator.transition(
            project_id="project-1",
            lifecycle_id=lifecycle2.lifecycle_id,
            to_state="EXECUTION_READY",
            command_id="ready",
            actor="user",
            source_command="ready",
        )


def test_legacy_embedded_observation_payload_is_rejected_at_schema_cutover():
    with pytest.raises(ValidationError, match="observation"):
        AgentLifecycleRecord.model_validate(
            {
                "schema_version": 5,
                "lifecycle_id": "legacy-task",
                "project_id": "project-1",
                "state": "SUCCEEDED",
                "observation": {"summary_status": "SUCCESS"},
            }
        )


def test_retry_is_quota_risk_and_contract_bound(tmp_path):
    store, service, ticket, orchestrator, lifecycle = _prepared(tmp_path, retry_quota=1)
    _, _, lifecycle = _dispatch(service, ticket, orchestrator, lifecycle)
    lifecycle = orchestrator.transition(
        project_id="project-1",
        lifecycle_id=lifecycle.lifecycle_id,
        to_state="FAILED",
        command_id="run-failed",
        actor="observer",
        source_command="observe",
    )
    proposal = orchestrator.propose_retry(
        project_id="project-1",
        lifecycle_id=lifecycle.lifecycle_id,
        command_id="retry-proposal",
        actor="diagnoser",
        node_ids=["data_inspection"],
        backend_ids=["python"],
        params={},
        input_roots=list(ticket.input_roots),
        output_roots=list(ticket.output_roots),
        classifier="transient_io",
        risk="low",
    )
    assert proposal.state == "RETRY_PROPOSED"
    assert proposal.retry_proposal is not None
    assert proposal.retry_proposal.requires_approval is True

    _, service2, ticket2, orchestrator2, lifecycle2 = _prepared(tmp_path / "changed", retry_quota=1)
    _, _, lifecycle2 = _dispatch(service2, ticket2, orchestrator2, lifecycle2)
    lifecycle2 = orchestrator2.transition(
        project_id="project-1",
        lifecycle_id=lifecycle2.lifecycle_id,
        to_state="FAILED",
        command_id="failed-changed",
        actor="observer",
        source_command="observe",
    )
    changed = orchestrator2.propose_retry(
        project_id="project-1",
        lifecycle_id=lifecycle2.lifecycle_id,
        command_id="changed-contract",
        actor="diagnoser",
        node_ids=["new_node"],
        backend_ids=["python"],
        params={"changed": True},
        input_roots=list(ticket2.input_roots),
        output_roots=list(ticket2.output_roots),
        classifier="unknown",
        risk="low",
    )
    assert changed.state == "PLAN_DRAFTED"
    assert changed.reviewed_plan_id is None
    assert changed.execution_ticket_id is None

    _, service3, ticket3, orchestrator3, lifecycle3 = _prepared(tmp_path / "quota", retry_quota=0)
    _, _, lifecycle3 = _dispatch(service3, ticket3, orchestrator3, lifecycle3)
    lifecycle3 = orchestrator3.transition(
        project_id="project-1",
        lifecycle_id=lifecycle3.lifecycle_id,
        to_state="FAILED",
        command_id="failed-quota",
        actor="observer",
        source_command="observe",
    )
    with pytest.raises(SafetyError, match="LIFECYCLE_RETRY_QUOTA_EXCEEDED"):
        orchestrator3.propose_retry(
            project_id="project-1",
            lifecycle_id=lifecycle3.lifecycle_id,
            command_id="quota-exceeded",
            actor="diagnoser",
            node_ids=["data_inspection"],
            backend_ids=["python"],
            params={},
            input_roots=list(ticket3.input_roots),
            output_roots=list(ticket3.output_roots),
            classifier="transient_io",
            risk="low",
        )


def test_lifecycle_api_is_project_scoped_and_queryable(tmp_path):
    store = _store(tmp_path)
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        client = TestClient(app)
        created = client.post(
            "/api/projects/project-1/agent-lifecycles",
            json={"command_id": "api-create", "actor": "user"},
        )
        assert created.status_code == 200
        lifecycle_id = created.json()["lifecycle"]["lifecycle_id"]
        advanced = client.post(
            f"/api/projects/project-1/agent-lifecycles/{lifecycle_id}/commands",
            json={"command_id": "api-context", "action": "context_ready", "actor": "user"},
        )
        assert advanced.status_code == 200
        assert advanced.json()["lifecycle"]["state"] == "CONTEXT_READY"
        drafted = client.post(
            f"/api/projects/project-1/agent-lifecycles/{lifecycle_id}/commands",
            json={
                "command_id": "api-draft",
                "action": "plan_drafted",
                "actor": "user",
                "reviewed_plan_id": "reviewed-1",
                "goal_contract_id": "goal-1",
                "goal_contract_hash": "goal-hash-1",
            },
        )
        assert drafted.status_code == 200
        assert drafted.json()["lifecycle"]["state"] == "PLAN_DRAFTED"
        assert drafted.json()["lifecycle"]["goal_contract_id"] == "goal-1"
        assert drafted.json()["lifecycle"]["goal_contract_hash"] == "goal-hash-1"
        queried = client.get(f"/api/projects/project-1/agent-lifecycles/{lifecycle_id}")
        assert queried.status_code == 200
        assert len(queried.json()["events"]) == 3
        assert (
            client.get(f"/api/projects/project-2/agent-lifecycles/{lifecycle_id}").status_code
            == 404
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)
