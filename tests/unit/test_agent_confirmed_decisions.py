"""Business-question deduplication is distinct from command/action replay."""
from datetime import UTC, datetime

import pytest

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_planning_action_service import AgentPlanningActionService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.unit.test_agent_harness_service import _store, _lifecycle, _decision, _service, Adapter
from tests.unit.test_agent_task_commands import AgentTaskCommandService


def _confirmed(tmp_path):
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    harness = _service(store, Adapter(_decision()))
    harness.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")
    first = harness.run_one(lifecycle=lifecycle, actor="user")
    assert first.lifecycle.pending_decision_batch is not None
    planning = AgentTaskCommandService(store)._planning
    answered = planning.answer(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id,
        batch_id=first.lifecycle.pending_decision_batch.batch_id,
        answers=[{"item_id": "atlas", "value": "aal"}], command_id="answer", actor="user",
    )
    assert answered.state == "PLAN_DRAFTED"
    return store, harness, answered


def _proposal(store, harness, lifecycle, *, decision=None):
    action = _decision().model_copy(update={
        "expected_state": lifecycle.state, "decision": decision or _decision().decision,
    })
    evidence = AgentEvidenceService(store).build_snapshot(
        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id,
    )
    projected = AgentEvidenceService.select_for_purpose(evidence, purpose="plan_draft")
    previous = store.list_agent_harness_actions(harness.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based").attempt_id)[0]
    record = previous.model_copy(update={
        "action_id": "different-model-action", "step_id": "different-step", "status": "accepted",
        "expected_state": lifecycle.state, "action_hash": stable_hash(action.model_dump(mode="json")),
        "action_payload": action.model_dump(mode="json"), "evidence_snapshot_hash": projected.snapshot_hash,
        "context_purpose": "plan_draft", "completed_at": None, "created_at": datetime.now(UTC),
    })
    store.add_agent_harness_action(record)
    return action, record


def test_distinct_model_action_does_not_reask_current_confirmed_answer(tmp_path):
    store, harness, answered = _confirmed(tmp_path)
    action, record = _proposal(store, harness, answered)
    events_before = len(store.list_agent_lifecycle_events(answered.lifecycle_id))
    result = harness.planning_action_service.apply(
        lifecycle_id=answered.lifecycle_id, action=action, actor="test", action_record=record,
    )
    assert result.lifecycle.pending_decision_batch is None
    assert result.attempt_status == "READY"
    assert result.action_result_code == "AGENT_DECISION_ALREADY_CONFIRMED"
    ledger = store.list_agent_harness_actions(record.attempt_id)[-1]
    assert ledger.status == "rejected" and ledger.error_code == result.action_result_code
    assert ledger.decision_batch_id is None
    assert len(store.list_agent_lifecycle_events(answered.lifecycle_id)) == events_before + 1
    assert store.list_execution_tickets("project-1") == []
    assert store.list_run_links("project-1") == []


@pytest.mark.parametrize("change", ["kind", "value", "evidence", "expired", "revoked", "unconfirmed"])
def test_changed_or_unconfirmed_binding_still_requires_a_decision(tmp_path, change):
    store, harness, lifecycle = _confirmed(tmp_path)
    context = dict(lifecycle.command_context)
    decision = _decision().decision
    if change == "kind":
        decision = decision.model_copy(update={"kind": "template"})
    elif change == "value":
        context["science_answers"] = {"atlas": "other"}
    elif change == "evidence":
        store.update_project_metadata("project-1", {"subject_count": 2})
    elif change == "unconfirmed":
        context.pop("confirmed_decisions", None)
    else:
        confirmations = dict(context.get("confirmed_decisions") or {})
        if "atlas" in confirmations:
            confirmations["atlas"] = {**confirmations["atlas"], **(
                {"expires_at": "2000-01-01T00:00:00Z"} if change == "expired" else {"status": "revoked"}
            )}
        context["confirmed_decisions"] = confirmations
    with store._connect() as conn:
        conn.execute("UPDATE agent_lifecycles SET payload=? WHERE lifecycle_id=?", (
            lifecycle.model_copy(update={"command_context": context}).model_dump_json(), lifecycle.lifecycle_id,
        ))
    lifecycle = store.get_agent_lifecycle(lifecycle.lifecycle_id)
    action, record = _proposal(store, harness, lifecycle, decision=decision)
    result = harness.planning_action_service.apply(
        lifecycle_id=lifecycle.lifecycle_id, action=action, actor="test", action_record=record,
    )
    assert result.lifecycle.pending_decision_batch is not None
    assert result.attempt_status == "WAITING_FOR_USER"


def test_provider_reasks_are_bounded_and_do_not_interrupt_confirmed_work(tmp_path):
    store, harness, lifecycle = _confirmed(tmp_path)
    harness.adapter.action = _decision().model_copy(update={"expected_state": "PLAN_DRAFTED"})
    harness.prepare_resume(lifecycle=lifecycle, provider_ref="rule_based")
    for _ in range(8):
        result = harness.run_one(lifecycle=store.get_agent_lifecycle(lifecycle.lifecycle_id), actor="user")
        assert result.lifecycle.pending_decision_batch is None
        if result.attempt.status == "STOPPED":
            break
    else:
        pytest.fail("Repeated business rejection must consume the finite Harness budget")
    actions = store.list_agent_harness_actions(result.attempt.attempt_id)
    assert len({action.action_id for action in actions}) == len(actions)
    assert all(action.status == "rejected" and action.error_code == "AGENT_DECISION_ALREADY_CONFIRMED" for action in actions[1:])
    assert store.list_execution_tickets("project-1") == []


def test_duplicate_commit_crash_recovers_without_provider_replay(tmp_path, monkeypatch):
    store, harness, lifecycle = _confirmed(tmp_path)
    harness.adapter.action = _decision().model_copy(update={"expected_state": "PLAN_DRAFTED"})
    harness.prepare_resume(lifecycle=lifecycle, provider_ref="rule_based")
    update = store.update_agent_harness_step

    def crash(step):
        if step.action_result_code == "AGENT_DECISION_ALREADY_CONFIRMED":
            raise SystemExit("simulated process loss after business commit")
        return update(step)

    monkeypatch.setattr(store, "update_agent_harness_step", crash)
    with pytest.raises(SystemExit):
        harness.run_one(lifecycle=lifecycle, actor="user")
    attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
    store.update_agent_harness_attempt(
        attempt.model_copy(update={"lease_expires_at": datetime(2000, 1, 1, tzinfo=UTC)}),
        expected_status="RUNNING", expected_step_no=attempt.next_step_no,
        expected_context_hash=attempt.context_hash,
    )
    reopened = SQLiteDesktopStore(store.db_path)
    adapter = Adapter(harness.adapter.action)
    recovered = _service(reopened, adapter).run_one(
        lifecycle=reopened.get_agent_lifecycle(lifecycle.lifecycle_id), actor="test", lease_owner="new-owner",
    )
    assert adapter.calls == 0
    assert recovered.attempt.status == "READY"
    assert recovered.lifecycle.pending_decision_batch is None
    assert len(reopened.list_agent_harness_actions(attempt.attempt_id)) == 2
    assert reopened.list_agent_harness_steps(attempt.attempt_id)[-1].action_result_code == "AGENT_DECISION_ALREADY_CONFIRMED"


@pytest.mark.parametrize("expired", [False, True])
def test_mixed_planner_batch_only_uses_current_confirmations(tmp_path, expired):
    store, harness, lifecycle = _confirmed(tmp_path)
    planning = AgentTaskCommandService(store)._planning
    context = dict(lifecycle.command_context)
    if expired:
        context["confirmed_decisions"] = {"atlas": {**context["confirmed_decisions"]["atlas"], "expires_at": "2000-01-01T00:00:00Z"}}
    plan = {"nodes": [{"id": "functional_connectivity_subject", "params": {}}], "metadata": {"science_decisions": {"atlas_candidates": [{"path": "aal", "name": "AAL", "checksum": "a" * 64, "license": "CC0"}], "global_signal_regression_required": True}}}
    evidence = AgentEvidenceService(store).build_snapshot(project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id)
    batch = planning._decision_batch(plan, context, {}, evidence.snapshot_hash, None, lifecycle)
    assert {item.kind for item in batch.items} == ({"atlas", "global_signal_regression"} if expired else {"global_signal_regression"})
