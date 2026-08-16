from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.main import app
from src.backend.app.runtime.execution_gateway import current_allowlist_hash
from src.backend.app.services.execution_ticket_service import (
    ExecutionTicketService,
    calculate_ticket_hash,
)
from src.backend.app.services.recovery_execution_service import RecoveryExecutionService
from tests.helpers_phase8 import build_recovery_fixture


def _successful_executor(calls: list[str]):
    def execute(*, project_config_path: str, pipeline_path: str, execution_context):
        calls.append(execution_context.ticket.execution_ticket_id)
        config = yaml.safe_load(Path(project_config_path).read_text(encoding="utf-8"))
        pipeline = yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8"))
        run_id = pipeline["execution"]["run_id"]
        work_dir = Path(config["runtime"]["work_dir"])
        state_dir = work_dir / "states" / run_id / "contract_smoke"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "project.json").write_text(
            json.dumps(
                {
                    "node": "contract_smoke",
                    "subject": "project",
                    "status": "SUCCESS",
                    "attempt": 1,
                    "backend": "python",
                    "contract_version": "1.0.0",
                    "outputs": [],
                    "errors": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        output_root = Path(config["runtime"]["derivatives_dir"]).parent
        summary_path = output_root / "pipeline_summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "status": "SUCCESS",
                    "nodes_total": 1,
                    "nodes_succeeded": 1,
                    "nodes_failed": 0,
                    "nodes_skipped": 0,
                    "errors": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return {"status": "SUCCESS", "summary_path": str(summary_path)}

    return execute


def _approve(fixture, service: RecoveryExecutionService):
    return service.approve(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="approve-recovery",
        actor="local-reviewer",
    )


def test_recovery_child_executes_once_in_isolated_output_and_closes_goal_loop(tmp_path):
    fixture = build_recovery_fixture(tmp_path)
    original_artifact = fixture.outputs / "successful-parent-artifact.txt"
    original_artifact.write_text("parent remains immutable", encoding="utf-8")
    calls: list[str] = []
    service = RecoveryExecutionService(fixture.store)
    ready, approval = _approve(fixture, service)
    assert ready.state == "RECOVERY_READY"
    assert approval.status == "active"

    lifecycle, attempt, result = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="execute-recovery",
        actor="local-reviewer",
        executor=_successful_executor(calls),
    )
    assert result and result["status"] == "SUCCESS"
    assert lifecycle.state == "GOAL_SATISFIED"
    assert attempt.status == "EVALUATED"
    assert attempt.goal_evaluation_status == "satisfied"
    assert attempt.observation_id and attempt.goal_evaluation_id
    assert original_artifact.read_text(encoding="utf-8") == "parent remains immutable"
    assert list(fixture.rawdata.rglob("*")) == []
    child = fixture.store.get_execution_ticket(attempt.child_execution_ticket_id or "")
    assert child is not None and child.ticket_kind == "recovery_child"
    assert child.execution_environment_snapshot_id == fixture.parent.execution_environment_snapshot_id
    assert child.execution_environment_hash == fixture.parent.execution_environment_hash
    assert child.execution_environment_snapshot_id == fixture.parent.execution_environment_snapshot_id
    assert child.execution_environment_hash == fixture.parent.execution_environment_hash
    assert child.status == "consumed"
    assert Path(child.output_roots[0]) != Path(fixture.parent.output_roots[0])
    assert Path(child.output_roots[0]).is_relative_to(Path(fixture.parent.output_roots[0]))
    assert calls == [child.execution_ticket_id]

    replay_lifecycle, replay_attempt, replay_result = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="execute-recovery",
        actor="local-reviewer",
        executor=_successful_executor(calls),
    )
    assert replay_lifecycle.state == "GOAL_SATISFIED"
    assert replay_attempt.recovery_attempt_id == attempt.recovery_attempt_id
    assert replay_result is None
    assert calls == [child.execution_ticket_id]


def test_child_ticket_rejects_cross_project_and_duplicate_consumption_with_audit(tmp_path):
    fixture = build_recovery_fixture(tmp_path)
    calls: list[str] = []
    service = RecoveryExecutionService(fixture.store)
    _approve(fixture, service)
    _, attempt, _ = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="execute-no-close",
        actor="local-reviewer",
        executor=_successful_executor(calls),
        close_loop=False,
    )
    child = fixture.store.get_execution_ticket(attempt.child_execution_ticket_id or "")
    assert child is not None
    ticket_service = ExecutionTicketService(fixture.store)
    common = {
        "execution_ticket_id": child.execution_ticket_id,
        "reviewed_plan_id": child.reviewed_plan_id,
        "plan_hash": child.plan_hash,
        "goal_contract_hash": child.goal_contract_hash,
        "evaluation_policy_version": child.evaluation_policy_version,
        "approval_summary_hash": child.approval_summary_hash,
        "memory_context_hash": child.memory_context_hash,
        "scope_hash": child.scope_hash,
        "allowlist_hash": current_allowlist_hash(),
        "normalized_params_hash": child.normalized_params_hash,
        "contract_versions": child.contract_versions,
        "project_config_path": child.project_config_path,
        "pipeline_path": child.pipeline_path,
    }
    with pytest.raises(SafetyError, match="EXECUTION_TICKET_PROJECT_MISMATCH"):
        ticket_service.validate(project_id="other-project", **common)
    with pytest.raises(SafetyError, match="EXECUTION_TICKET_REPLAYED"):
        ticket_service.validate(project_id=fixture.project_id, **common)
    reasons = [
        event.reason
        for event in fixture.store.list_execution_ticket_events(child.execution_ticket_id)
        if event.event_type == "rejected"
    ]
    assert "EXECUTION_TICKET_PROJECT_MISMATCH" in reasons
    assert "EXECUTION_TICKET_REPLAYED" in reasons


def test_restart_reconciliation_handoffs_without_repeat_dispatch(tmp_path):
    fixture = build_recovery_fixture(tmp_path)
    calls: list[str] = []
    service = RecoveryExecutionService(fixture.store)
    _approve(fixture, service)
    _, attempt, _ = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="execute-before-crash",
        actor="local-reviewer",
        executor=_successful_executor(calls),
        close_loop=False,
    )
    assert attempt.status == "EXECUTION_SUCCEEDED"
    recovered = RecoveryExecutionService(fixture.store).recover_incomplete_attempts(
        fixture.project_id, fixture.lifecycle_id
    )
    recovered_attempt = next(
        item for item in recovered if item.recovery_attempt_id == attempt.recovery_attempt_id
    )
    assert recovered_attempt.status == "HANDOFF"
    assert fixture.store.get_agent_lifecycle(fixture.lifecycle_id).state == "HUMAN_HANDOFF"

    _, replay, result = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="execute-before-crash",
        actor="local-reviewer",
        executor=_successful_executor(calls),
    )
    assert replay.status == "HANDOFF"
    assert result is None
    assert len(calls) == 1


def test_quota_persistence_failure_handoffs_before_runner(tmp_path, monkeypatch):
    fixture = build_recovery_fixture(tmp_path)
    calls: list[str] = []
    service = RecoveryExecutionService(fixture.store)
    _approve(fixture, service)

    def fail_reservation(_record):
        raise RuntimeError("simulated reservation persistence failure")

    monkeypatch.setattr(fixture.store, "reserve_recovery_quota", fail_reservation)
    lifecycle, attempt, result = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="execute-quota-failure",
        actor="local-reviewer",
        executor=_successful_executor(calls),
    )
    assert lifecycle.state == "HUMAN_HANDOFF"
    assert attempt.status == "HANDOFF"
    assert attempt.remaining_goal_gap_ids == ("pipeline-complete",)
    assert attempt.safe_human_actions
    assert result is None
    assert calls == []


def test_ticket_audit_failure_after_consumption_still_handoffs_before_runner(tmp_path, monkeypatch):
    fixture = build_recovery_fixture(tmp_path)
    calls: list[str] = []
    service = RecoveryExecutionService(fixture.store)
    _approve(fixture, service)
    original = fixture.store.add_execution_ticket_event

    def fail_consumed_event(event):
        if event.event_type == "consumed":
            raise RuntimeError("simulated audit failure")
        return original(event)

    monkeypatch.setattr(fixture.store, "add_execution_ticket_event", fail_consumed_event)
    lifecycle, attempt, result = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id="execute-audit-failure",
        actor="local-reviewer",
        executor=_successful_executor(calls),
    )
    assert lifecycle.state == "HUMAN_HANDOFF"
    assert attempt.status == "HANDOFF"
    assert result is None
    assert calls == []


@pytest.mark.parametrize(
    ("updates", "expected_reason"),
    [
        ({"recovery_attempt_id": "other-attempt"}, "RECOVERY_CHILD_ATTEMPT_INVALID"),
        (
            {"recovery_node_ids": ("contract_smoke", "data_inspection")},
            "RECOVERY_CHILD_ATTEMPT_INVALID",
        ),
        (
            {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
            "EXECUTION_TICKET_EXPIRED",
        ),
    ],
)
def test_child_cross_attempt_and_expiry_are_rejected_before_runner(
    tmp_path, monkeypatch, updates, expected_reason
):
    fixture = build_recovery_fixture(tmp_path)
    calls: list[str] = []
    service = RecoveryExecutionService(fixture.store)
    _approve(fixture, service)
    original_issue = service.ticket_service.issue_recovery_child

    def issue_tampered_child(**kwargs):
        child = original_issue(**kwargs)
        changed = child.model_copy(update={**updates, "canonical_hash": "pending"})
        changed = changed.model_copy(update={"canonical_hash": calculate_ticket_hash(changed)})
        changed_payload = changed.model_dump(mode="json")
        changed_payload.pop("execution_ticket_id")
        fixture.store.update_execution_ticket(
            child.execution_ticket_id,
            **changed_payload,
        )
        return changed

    monkeypatch.setattr(service.ticket_service, "issue_recovery_child", issue_tampered_child)
    lifecycle, attempt, result = service.execute(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=fixture.candidate.candidate_id,
        command_id=f"execute-rejected-{expected_reason}",
        actor="local-reviewer",
        executor=_successful_executor(calls),
    )
    assert lifecycle.state == "HUMAN_HANDOFF"
    assert attempt.status == "HANDOFF"
    assert result is None and calls == []
    child_events = fixture.store.list_execution_ticket_events(
        attempt.child_execution_ticket_id or ""
    )
    assert child_events[-1].reason == expected_reason


def test_recovery_intent_api_exposes_approval_and_attempt_queries_only(tmp_path):
    fixture = build_recovery_fixture(tmp_path)
    app.dependency_overrides[get_project_store] = lambda: fixture.store
    client = TestClient(app)
    base = f"/api/projects/{fixture.project_id}/agent-lifecycles/{fixture.lifecycle_id}"
    try:
        response = client.post(
            f"{base}/recovery-proposals/{fixture.proposal.recovery_proposal_id}/approve",
            json={
                "command_id": "api-approve",
                "actor": "local-reviewer",
                "candidate_id": fixture.candidate.candidate_id,
            },
        )
        assert response.status_code == 200
        approval_id = response.json()["recovery_approval"]["recovery_approval_id"]
        attempts = client.get(f"{base}/recovery-attempts")
        assert attempts.status_code == 200
        assert attempts.json()["recovery_attempts"] == []
        revoked = client.post(
            f"{base}/recovery-approvals/{approval_id}/revoke",
            json={"command_id": "api-revoke", "actor": "local-reviewer"},
        )
        assert revoked.status_code == 200
        assert revoked.json()["recovery_approval"]["status"] == "revoked"
    finally:
        app.dependency_overrides.clear()
