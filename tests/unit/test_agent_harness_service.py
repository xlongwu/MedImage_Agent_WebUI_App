from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.agent_model_adapter import (
    ActionCallMetadata,
    ActionProposal,
    AgentModelInvalidOutputError,
)
from src.backend.app.runtime.agent_harness_scheduler import AgentHarnessScheduler
from src.backend.app.schemas.agent_harness import ActionEnvelope
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_context_service import HarnessContextBuilder
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.unit.test_agent_task_read_model import _terminal_evidence


class Adapter:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.calls = 0

    def propose_action(self, **_kwargs):
        self.calls += 1
        action = self.actions.pop(0)
        return action if isinstance(action, ActionProposal) else ActionProposal.rule_based(action)


def _network_proposal(envelope: ActionEnvelope, *, input_tokens: int | None = None, output_tokens: int | None = None) -> ActionProposal:
    return ActionProposal(
        envelope=envelope,
        metadata=ActionCallMetadata(
            provider="openai_compatible", model="gpt-test", endpoint_class="chat_completions",
            response_hash="response-hash", input_tokens=input_tokens, output_tokens=output_tokens,
            cached_input_tokens=None, latency_ms=12, provider_request_id="req-test",
            network_called=True,
        ),
    )


def _store(tmp_path) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "harness.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=1, current_pipeline_id="pipeline", sequences=[], scans_count=1, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    return store


def _created(store):
    return AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user", goal_text="Make a plan")


def test_request_decision_is_persisted_and_waits_for_user(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(
        kind="request_decision", reason="Need atlas", expected_state="CREATED",
        payload={"kind": "atlas", "question": "Choose atlas", "impact": "Changes regions", "options": [{"id": "aal", "label": "AAL"}]},
    ))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.lifecycle.state == "WAITING_FOR_SCIENCE_DECISION"
    assert result.attempt.status == "WAITING_FOR_USER"
    assert store.list_agent_harness_steps(result.attempt.attempt_id)[0].kind == "request_decision"


def test_planning_skill_refs_and_hashes_are_persisted_without_markdown(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(kind="read_evidence", reason="Read evidence", expected_state="CREATED"))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    context = store.get_agent_harness_context(result.attempt.context_hash)
    step = store.list_agent_harness_steps(result.attempt.attempt_id)[0]
    assert context is not None
    assert [ref.skill_id for ref in context.skill_refs] == ["planning_evidence_review.v1"]
    assert [ref.skill_id for ref in step.skill_refs] == ["planning_evidence_review.v1"]
    assert step.model_calls[0].skill_hashes == (context.skill_refs[0].content_hash,)
    assert "Do not infer missing data" not in str(context.model_dump())


def test_invalid_or_stale_action_stops_without_second_model_call(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(kind="finish", reason="wrong state", expected_state="PLAN_DRAFTED"))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "STOPPED"
    assert result.attempt.terminal_reason == "AGENT_HARNESS_STALE_ACTION"
    assert adapter.calls == 1


def test_budget_exhaustion_happens_before_model_call(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(kind="finish", reason="done", expected_state="CREATED"))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True, max_model_calls=1), adapter=adapter)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based").model_copy(update={"model_calls_used": 1})
    store.update_agent_harness_attempt(attempt, expected_status="READY")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.terminal_reason == "AGENT_HARNESS_MODEL_CALL_BUDGET_EXHAUSTED"
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("config_updates", "attempt_updates", "reason"),
    [
        ({"max_steps": 1}, {"steps_used": 1}, "AGENT_HARNESS_STEP_BUDGET_EXHAUSTED"),
        ({"max_action_proposals": 1}, {"action_proposals_used": 1}, "AGENT_HARNESS_ACTION_PROPOSAL_BUDGET_EXHAUSTED"),
    ],
)
def test_hard_budget_precheck_stops_before_provider_call(tmp_path, config_updates, attempt_updates, reason) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(kind="finish", reason="done", expected_state="CREATED"))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True, **config_updates), adapter=adapter)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based").model_copy(update=attempt_updates)
    store.update_agent_harness_attempt(attempt, expected_status="READY")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.terminal_reason == reason
    assert adapter.calls == 0


def test_reported_token_limit_stops_after_current_redacted_step(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(_network_proposal(
        ActionEnvelope(kind="read_evidence", reason="read", expected_state="CREATED"),
        input_tokens=4, output_tokens=2,
    ))
    service = AgentHarnessService(
        store, config=AgentHarnessConfig(enabled=True, max_input_tokens=4), adapter=adapter,
    )
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="openai_compatible")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "STOPPED"
    assert result.attempt.terminal_reason == "AGENT_HARNESS_INPUT_TOKEN_BUDGET_EXHAUSTED"
    assert result.attempt.input_tokens_used == 4


def test_recovery_budget_rejects_proposal_without_handler_side_effect(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store).model_copy(update={"state": "DIAGNOSING"})
    adapter = Adapter(ActionEnvelope(kind="propose_recovery", reason="recover", expected_state="DIAGNOSING"))
    calls: list[str] = []
    service = AgentHarnessService(
        store, config=AgentHarnessConfig(enabled=True, max_recovery_attempts=2), adapter=adapter,
        recovery_proposer=lambda **_kwargs: calls.append("recovery"),
    )
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based").model_copy(
        update={"recovery_attempts_used": 2}
    )
    store.update_agent_harness_attempt(attempt, expected_status="READY")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.terminal_reason == "AGENT_HARNESS_RECOVERY_BUDGET_EXHAUSTED"
    assert calls == []


def test_invalid_json_gets_one_repair_and_records_both_network_calls(tmp_path) -> None:
    class RepairAdapter:
        def __init__(self) -> None:
            self.repairs: list[bool] = []

        def propose_action(self, *, repair: bool, **_kwargs):
            self.repairs.append(repair)
            if not repair:
                raise AgentModelInvalidOutputError(
                    "AGENT_HARNESS_MODEL_OUTPUT_INVALID",
                    ActionCallMetadata(
                        provider="openai_compatible", model="gpt-test", endpoint_class="chat_completions",
                        response_hash="invalid-hash", input_tokens=7, output_tokens=2,
                        cached_input_tokens=None, latency_ms=10, provider_request_id="req-invalid",
                        network_called=True,
                    ),
                )
            return _network_proposal(ActionEnvelope(kind="finish", reason="fixed", expected_state="CREATED"), input_tokens=9, output_tokens=3)

    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = RepairAdapter()
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert adapter.repairs == [False, True]
    assert result.attempt.model_calls_used == 2
    assert result.attempt.repairs_used == 1
    assert result.attempt.input_tokens_used == 16
    step = store.list_agent_harness_steps(result.attempt.attempt_id)[0]
    assert [call.status for call in step.model_calls] == ["invalid_output", "succeeded"]
    assert all("sk-" not in str(call.model_dump(mode="json")) for call in step.model_calls)
    assert result.attempt.status == "FINISHED"


def test_run_one_leaves_a_follow_up_action_for_a_later_wakeup(tmp_path) -> None:
    """Characterization: one command must not consume more than one action."""
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(
        ActionEnvelope(kind="read_evidence", reason="Read project summary", expected_state="CREATED"),
        ActionEnvelope(kind="finish", reason="This must wait for another wakeup", expected_state="CREATED"),
    )
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "READY"
    assert result.attempt.next_step_no == 2
    assert adapter.calls == 1
    assert len(store.list_agent_harness_steps(attempt.attempt_id)) == 1


def test_dynamic_last_action_and_budget_rebuild_context_instead_of_reusing_attempt_hash(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(
        ActionEnvelope(kind="read_evidence", reason="Read current evidence", expected_state="CREATED"),
        ActionEnvelope(kind="finish", reason="Done", expected_state="CREATED"),
    )
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    first = service.run_one(lifecycle=lifecycle, actor="user")
    second = service.run_one(
        lifecycle=store.get_agent_lifecycle(lifecycle.lifecycle_id), actor="user"
    )

    assert first.attempt.context_hash != second.attempt.context_hash
    assert store.get_agent_harness_context(first.attempt.context_hash).schema_version == 2
    assert store.get_agent_harness_context(second.attempt.context_hash).schema_version == 2


def test_context_limit_stops_before_provider_call(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(kind="finish", reason="must not run", expected_state="CREATED"))
    builder = HarnessContextBuilder()
    builder.MAX_BYTES = 32
    service = AgentHarnessService(
        store, config=AgentHarnessConfig(enabled=True), adapter=adapter, context_builder=builder,
    )
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.terminal_reason == "AGENT_CONTEXT_LIMIT_EXCEEDED"
    assert adapter.calls == 0


def test_run_until_blocked_completes_three_safe_actions_in_one_wakeup(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(
        ActionEnvelope(kind="read_evidence", reason="read one", expected_state="CREATED"),
        ActionEnvelope(kind="read_evidence", reason="read two", expected_state="CREATED"),
        ActionEnvelope(kind="finish", reason="done", expected_state="CREATED"),
    )
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_until_blocked(
        lifecycle=lifecycle,
        actor="user",
        wake_reason="create",
        lease_owner="test-loop",
    )

    assert result.outcome == "finished"
    assert result.steps_run == 3
    assert result.attempt.status == "FINISHED"
    assert result.attempt.last_wake_reason == "create"
    assert result.attempt.last_progress_at is not None
    assert adapter.calls == 3
    assert len(store.list_agent_harness_steps(attempt.attempt_id)) == 3


def test_run_until_blocked_yields_after_configured_wakeup_budget(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(
        ActionEnvelope(kind="read_evidence", reason="read one", expected_state="CREATED"),
        ActionEnvelope(kind="read_evidence", reason="read two", expected_state="CREATED"),
        ActionEnvelope(kind="read_evidence", reason="read three", expected_state="CREATED"),
        ActionEnvelope(kind="finish", reason="done", expected_state="CREATED"),
    )
    service = AgentHarnessService(
        store,
        config=AgentHarnessConfig(enabled=True, max_steps_per_wakeup=3),
        adapter=adapter,
    )
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    first = service.run_until_blocked(
        lifecycle=lifecycle,
        actor="user",
        wake_reason="create",
        lease_owner="test-yield",
    )
    second = service.run_until_blocked(
        lifecycle=lifecycle,
        actor="user",
        wake_reason="fairness_yield",
        lease_owner="test-yield",
    )

    assert first.outcome == "yielded"
    assert first.steps_run == 3
    assert first.attempt.status == "READY"
    assert first.attempt.yield_count == 1
    assert second.outcome == "finished"
    assert second.steps_run == 1
    assert adapter.calls == 4


def test_scheduler_gives_two_ready_lifecycles_one_fair_wakeup_each(tmp_path) -> None:
    store = _store(tmp_path)
    first = _created(store)
    second = AgentOrchestrator(store).create(
        project_id="project-1", command_id="create-second", actor="user", goal_text="Make another plan"
    )
    adapter = Adapter(
        ActionEnvelope(kind="read_evidence", reason="first", expected_state="CREATED"),
        ActionEnvelope(kind="read_evidence", reason="second", expected_state="CREATED"),
    )
    config = AgentHarnessConfig(enabled=True, max_steps_per_wakeup=1)
    harness = AgentHarnessService(store, config=config, adapter=adapter)
    harness.ensure_attempt(lifecycle=first, provider_ref="rule_based")
    harness.ensure_attempt(lifecycle=second, provider_ref="rule_based")
    scheduler = AgentHarnessScheduler(
        store, config=config, harness_service=harness, start_workers=False
    )

    assert scheduler.wake(project_id="project-1", lifecycle_id=first.lifecycle_id, reason="create")
    assert scheduler.wake(project_id="project-1", lifecycle_id=second.lifecycle_id, reason="create")
    assert scheduler.run_pending_batch() == (first.lifecycle_id, second.lifecycle_id)

    assert store.get_agent_harness_attempt(first.lifecycle_id).yield_count == 1
    assert store.get_agent_harness_attempt(second.lifecycle_id).yield_count == 1
    assert adapter.calls == 2


def test_shutdown_refuses_new_claims_without_mutating_an_attempt(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    config = AgentHarnessConfig(enabled=True)
    harness = AgentHarnessService(
        store,
        config=config,
        adapter=Adapter(ActionEnvelope(kind="finish", reason="done", expected_state="CREATED")),
    )
    attempt = harness.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")
    scheduler = AgentHarnessScheduler(
        store, config=config, harness_service=harness, start_workers=False
    )

    assert scheduler.shutdown()
    assert not scheduler.wake(project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, reason="create")
    assert store.get_agent_harness_attempt(lifecycle.lifecycle_id) == attempt


def test_startup_recovery_skips_waiting_and_canceled_attempts(tmp_path) -> None:
    """Characterization of the supported terminal path, not a recovery guarantee."""
    store = _store(tmp_path)
    waiting_lifecycle = _created(store)
    waiting_harness = AgentHarnessService(
        store,
        config=AgentHarnessConfig(enabled=True),
        adapter=Adapter(
            ActionEnvelope(
                kind="request_decision",
                reason="Need atlas",
                expected_state="CREATED",
                payload={"kind": "atlas", "question": "Choose atlas", "impact": "Changes regions"},
            )
        ),
    )
    waiting_harness.ensure_attempt(lifecycle=waiting_lifecycle, provider_ref="rule_based")
    waiting_harness.run_one(lifecycle=waiting_lifecycle, actor="user")

    canceled_lifecycle = AgentOrchestrator(store).create(
        project_id="project-1",
        command_id="create-canceled",
        actor="user",
        goal_text="Make a plan",
    )
    canceled_attempt = waiting_harness.ensure_attempt(
        lifecycle=canceled_lifecycle, provider_ref="rule_based"
    )
    AgentOrchestrator(store).cancel(
        project_id="project-1",
        lifecycle_id=canceled_lifecycle.lifecycle_id,
        command_id="cancel",
        actor="user",
    )
    waiting_harness.stop(lifecycle_id=canceled_lifecycle.lifecycle_id, reason="LIFECYCLE_CANCELED")

    processed = AgentHarnessScheduler(
        store, config=AgentHarnessConfig(enabled=True)
    ).recover_once_on_startup()

    assert processed == ()
    assert store.get_agent_harness_attempt(waiting_lifecycle.lifecycle_id).status == "WAITING_FOR_USER"
    assert store.get_agent_harness_attempt(canceled_lifecycle.lifecycle_id).attempt_id == canceled_attempt.attempt_id
    assert store.get_agent_harness_attempt(canceled_lifecycle.lifecycle_id).status == "STOPPED"


def test_run_reconciled_reflector_persists_only_safe_result_explanation(tmp_path) -> None:
    store = _store(tmp_path)
    observation, evaluation = _terminal_evidence(reload_status="failed", completeness="partial")
    now = datetime.now(UTC)
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="task-1",
        project_id="project-1",
        state="GOAL_SATISFIED",
        reviewed_plan_id="plan-1",
        execution_ticket_id="ticket-1",
        run_id="run-1",
        observation_id=observation.observation_id,
        goal_evaluation_id=evaluation.goal_evaluation_id,
        created_at=now,
        updated_at=now,
    )
    store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id="event-1",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            command_id="fixture",
            actor="test",
            source_command="fixture",
            occurred_at=now,
            from_state=None,
            to_state="GOAL_SATISFIED",
            observation_id=observation.observation_id,
            goal_evaluation_id=evaluation.goal_evaluation_id,
        ),
    )
    store.add_observation(observation)
    store.add_goal_evaluation(evaluation)
    service = AgentHarnessService(
        store,
        config=AgentHarnessConfig(enabled=True),
        adapter=Adapter(
            ActionEnvelope(
                kind="explain_result",
                reason="Explain deterministic evidence",
                expected_state="GOAL_SATISFIED",
                payload={"generated_text": "The run succeeded and is fully validated."},
            )
        ),
    )
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_until_blocked(
        lifecycle=lifecycle,
        actor="system",
        wake_reason="run_reconciled",
        wake_fingerprint="terminal-evidence-hash",
        lease_owner="reflector",
    )

    assert result.outcome == "finished"
    assert result.attempt.status == "FINISHED"
    assert result.attempt.last_wake_fingerprint == "terminal-evidence-hash"
    step = store.list_agent_harness_steps(attempt.attempt_id)[0]
    assert step.kind == "explain_result"
    assert step.generated_text is None
    assert step.action_result_code == "AGENT_EXPLANATION_CONFLICT"
    assert step.observation_ref == observation.observation_id
    assert step.evaluation_ref == evaluation.goal_evaluation_id
    explanation_events = [
        item
        for item in store.list_agent_lifecycle_events(lifecycle.lifecycle_id)
        if item.source_command == "harness_result_explained"
    ]
    assert explanation_events[0].details["result_explanation_hash"] == step.result_explanation_hash
