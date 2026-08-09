from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import src.backend.app.services.execution_ticket_service as execution_ticket_service_module
from src.backend.app.api.dependencies import get_project_store
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.main import app
from src.backend.app.runtime.execution_gateway import (
    ExecutionGateway,
    VerifiedExecutionContext,
    current_allowlist_hash,
)
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.execution_ticket_service import (
    ExecutionTicketService,
    calculate_ticket_hash,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _service(tmp_path):
    return ExecutionTicketService(SQLiteDesktopStore(tmp_path / "state.sqlite"))


def _issue(service: ExecutionTicketService, tmp_path, **overrides):
    config = tmp_path / "project.yaml"
    pipeline = tmp_path / "pipeline.yaml"
    config.write_text("runtime: {}\n", encoding="utf-8")
    pipeline.write_text("nodes: []\n", encoding="utf-8")
    values = {
        "project_id": "project-1",
        "reviewed_plan_id": "reviewed-1",
        "plan_hash": "plan-hash",
        "goal_contract_hash": "goal-contract-hash",
        "evaluation_policy_version": "goal-evaluator-v1",
        "approval_summary_hash": "approval-1",
        "memory_context_hash": None,
        "approved_actor": "reviewer",
        "approved_node_ids": ["contract_smoke"],
        "approved_backend_ids": ["python"],
        "input_roots": [str(tmp_path / "inputs")],
        "output_roots": [str(tmp_path / "outputs")],
        "project_config_path": str(config),
        "pipeline_path": str(pipeline),
        "allowlist_hash": current_allowlist_hash(),
        "normalized_params_hash": "normalized-params-hash",
        "contract_versions": {"contract_smoke": "1.0.0"},
        "audit_id": "audit-1",
    }
    values.update(overrides)
    return service.issue(**values)


def _validate(service: ExecutionTicketService, ticket, **overrides):
    values = {
        "project_id": ticket.project_id,
        "reviewed_plan_id": ticket.reviewed_plan_id,
        "plan_hash": ticket.plan_hash,
        "approval_summary_hash": ticket.approval_summary_hash,
        "memory_context_hash": ticket.memory_context_hash,
        "scope_hash": ticket.scope_hash,
        "allowlist_hash": ticket.allowlist_hash,
        "normalized_params_hash": ticket.normalized_params_hash,
        "contract_versions": ticket.contract_versions,
        "project_config_path": ticket.project_config_path,
        "pipeline_path": ticket.pipeline_path,
    }
    values.update(overrides)
    return service.validate(ticket.execution_ticket_id, **values)


def test_ticket_persists_validates_and_consumes_once(tmp_path):
    service = _service(tmp_path)
    ticket = _issue(service, tmp_path)

    validated = _validate(service, ticket)
    consumed = service.consume(validated, idempotency_key="dispatch-1")

    assert consumed.status == "consumed"
    assert service.store.get_execution_ticket(ticket.execution_ticket_id).status == "consumed"
    event_types = [
        event.event_type
        for event in service.store.list_execution_ticket_events(ticket.execution_ticket_id)
    ]
    assert event_types == ["issued", "validated", "consumed"]
    with pytest.raises(SafetyError, match="EXECUTION_TICKET_REPLAYED"):
        _validate(service, ticket)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("project_id", "project-2", "EXECUTION_TICKET_PROJECT_MISMATCH"),
        ("plan_hash", "changed", "EXECUTION_TICKET_HASH_MISMATCH"),
        ("approval_summary_hash", "changed", "EXECUTION_TICKET_APPROVAL_MISMATCH"),
        ("memory_context_hash", "changed", "EXECUTION_TICKET_MEMORY_CONTEXT_MISMATCH"),
        ("scope_hash", "changed", "EXECUTION_TICKET_SCOPE_MISMATCH"),
        ("allowlist_hash", "changed", "EXECUTION_TICKET_ALLOWLIST_MISMATCH"),
        ("normalized_params_hash", "changed", "EXECUTION_TICKET_PARAMETER_HASH_MISMATCH"),
        (
            "contract_versions",
            {"contract_smoke": "2.0.0"},
            "EXECUTION_TICKET_CONTRACT_VERSION_MISMATCH",
        ),
        ("goal_contract_hash", "changed", "EXECUTION_TICKET_GOAL_CONTRACT_MISMATCH"),
        ("evaluation_policy_version", "changed", "EXECUTION_TICKET_EVALUATION_POLICY_MISMATCH"),
    ],
)
def test_ticket_binding_mismatch_is_rejected_and_audited(tmp_path, monkeypatch, field, value, code):
    fixed_time = datetime(2099, 1, 1, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_time if tz else fixed_time.replace(tzinfo=None)

    monkeypatch.setattr(execution_ticket_service_module, "datetime", FrozenDateTime)
    service = _service(tmp_path)
    ticket = _issue(service, tmp_path)

    with pytest.raises(SafetyError, match=code):
        _validate(service, ticket, **{field: value})

    events = service.store.list_execution_ticket_events(ticket.execution_ticket_id)
    assert events[-1].event_type == "rejected"
    assert events[-1].reason == code


def test_expired_revoked_and_tampered_tickets_fail_closed(tmp_path):
    service = _service(tmp_path)

    expired = _issue(service, tmp_path)
    expired_payload = expired.model_dump(mode="json")
    expired_payload["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    expired_payload["canonical_hash"] = "pending"
    expired_payload["canonical_hash"] = calculate_ticket_hash(expired_payload)
    expired_payload.pop("execution_ticket_id")
    service.store.update_execution_ticket(expired.execution_ticket_id, **expired_payload)
    with pytest.raises(SafetyError, match="EXECUTION_TICKET_EXPIRED"):
        _validate(service, expired)

    revoked = _issue(service, tmp_path, plan_hash="plan-hash-2")
    service.revoke(revoked.execution_ticket_id, reason="review withdrawn")
    with pytest.raises(SafetyError, match="EXECUTION_TICKET_REVOKED"):
        _validate(service, revoked)

    tampered = _issue(service, tmp_path, plan_hash="plan-hash-3")
    service.store.update_execution_ticket(
        tampered.execution_ticket_id,
        approved_node_ids=("spm_realign_subject",),
    )
    with pytest.raises(SafetyError, match="EXECUTION_TICKET_TAMPERED"):
        _validate(service, tampered)


def test_gateway_is_only_source_of_verified_runtime_context(tmp_path):
    service = _service(tmp_path)
    ticket = _issue(service, tmp_path)
    calls: list[VerifiedExecutionContext] = []

    def fake_executor(*, project_config_path, pipeline_path, execution_context):
        calls.append(execution_context)
        return {"status": "SUCCESS", "run_id": "run-1"}

    result, consumed = ExecutionGateway(service).dispatch(
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
        command_id="execute-command-1",
        run_id="run-1",
        executor=fake_executor,
    )

    assert result["status"] == "SUCCESS"
    assert consumed.status == "consumed"
    assert len(calls) == 1
    assert calls[0].ticket.execution_ticket_id == ticket.execution_ticket_id
    assert calls[0].dispatch.dispatch_id == result["dispatch_id"]
    dispatch = service.store.get_gateway_dispatch_by_ticket(ticket.execution_ticket_id)
    assert dispatch is not None
    assert dispatch.run_id == "run-1"
    assert [
        event.event_type
        for event in service.store.list_gateway_dispatch_events(dispatch.dispatch_id)
    ] == ["dispatch_started", "dispatch_succeeded"]

    replay, replayed_ticket = ExecutionGateway(service).dispatch(
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
        command_id="execute-command-1",
        run_id="run-1",
        executor=fake_executor,
    )
    assert replay == result
    assert replayed_ticket.status == "consumed"
    assert len(calls) == 1


def test_gateway_resumes_prepared_dispatch_but_never_replays_unknown_outcome(
    tmp_path, monkeypatch
):
    service = _service(tmp_path)
    ticket = _issue(service, tmp_path)
    gateway = ExecutionGateway(service)
    original_add_event = service.store.add_gateway_dispatch_event
    fail_once = True

    def fail_before_started(event):
        nonlocal fail_once
        if fail_once and event.event_type == "dispatch_started":
            fail_once = False
            raise OSError("simulated event write crash")
        return original_add_event(event)

    monkeypatch.setattr(service.store, "add_gateway_dispatch_event", fail_before_started)
    common = {
        "execution_ticket_id": ticket.execution_ticket_id,
        "project_id": ticket.project_id,
        "reviewed_plan_id": ticket.reviewed_plan_id,
        "plan_hash": ticket.plan_hash,
        "approval_summary_hash": ticket.approval_summary_hash,
        "memory_context_hash": ticket.memory_context_hash,
        "scope_hash": ticket.scope_hash,
        "normalized_params_hash": ticket.normalized_params_hash,
        "contract_versions": ticket.contract_versions,
        "project_config_path": ticket.project_config_path,
        "pipeline_path": ticket.pipeline_path,
        "command_id": "crash-window-command",
        "run_id": "run-crash-window",
    }
    with pytest.raises(Exception, match="GATEWAY_DISPATCH_EVENT_WRITE_FAILED"):
        gateway.dispatch(
            **common,
            executor=lambda **_: {"status": "SUCCESS", "run_id": "run-crash-window"},
        )

    calls = 0

    def crash_after_started(**kwargs):
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt("simulated process crash")

    monkeypatch.setattr(service.store, "add_gateway_dispatch_event", original_add_event)
    with pytest.raises(KeyboardInterrupt):
        gateway.dispatch(**common, executor=crash_after_started)
    assert calls == 1
    with pytest.raises(SafetyError, match="GATEWAY_DISPATCH_OUTCOME_UNKNOWN"):
        gateway.dispatch(**common, executor=crash_after_started)
    assert calls == 1


def test_gateway_rejects_before_executor_on_mismatch(tmp_path):
    service = _service(tmp_path)
    ticket = _issue(service, tmp_path)
    called = False

    def fake_executor(**kwargs):
        nonlocal called
        called = True
        return {"status": "SUCCESS"}

    with pytest.raises(SafetyError, match="EXECUTION_TICKET_HASH_MISMATCH"):
        ExecutionGateway(service).dispatch(
            execution_ticket_id=ticket.execution_ticket_id,
            project_id=ticket.project_id,
            reviewed_plan_id=ticket.reviewed_plan_id,
            plan_hash="wrong",
            approval_summary_hash=ticket.approval_summary_hash,
            memory_context_hash=ticket.memory_context_hash,
            scope_hash=ticket.scope_hash,
            normalized_params_hash=ticket.normalized_params_hash,
            contract_versions=ticket.contract_versions,
            project_config_path=ticket.project_config_path,
            pipeline_path=ticket.pipeline_path,
            command_id="execute-command-1",
            run_id="run-1",
            executor=fake_executor,
        )
    assert called is False


def test_ticket_and_rejection_events_are_queryable_by_project(tmp_path):
    service = _service(tmp_path)
    service.store.add_project(
        ProjectDetail(
            id="project-1",
            name="Ticket query",
            study_id="project-1",
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
    ticket = _issue(service, tmp_path)
    with pytest.raises(SafetyError):
        _validate(service, ticket, project_id="another-project")

    app.dependency_overrides[get_project_store] = lambda: service.store
    try:
        client = TestClient(app)
        response = client.get(
            f"/api/projects/project-1/execution-tickets/{ticket.execution_ticket_id}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["execution_ticket"]["execution_ticket_id"] == ticket.execution_ticket_id
        assert [event["event_type"] for event in body["events"]] == ["issued", "rejected"]

        wrong_project = client.get(
            f"/api/projects/another-project/execution-tickets/{ticket.execution_ticket_id}"
        )
        assert wrong_project.status_code == 404
    finally:
        app.dependency_overrides.pop(get_project_store, None)
