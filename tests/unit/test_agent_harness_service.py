from __future__ import annotations

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.runtime.agent_harness_scheduler import AgentHarnessScheduler
from src.backend.app.schemas.agent_harness import ActionEnvelope
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


class Adapter:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.calls = 0

    def propose_action(self, **_kwargs):
        self.calls += 1
        return self.actions.pop(0)


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

    assert result.attempt.terminal_reason == "AGENT_HARNESS_BUDGET_EXHAUSTED"
    assert adapter.calls == 0


def test_invalid_json_gets_one_repair_and_counts_both_model_calls(tmp_path) -> None:
    class RepairAdapter:
        def __init__(self) -> None:
            self.repairs: list[bool] = []

        def propose_action(self, *, repair: bool, **_kwargs):
            self.repairs.append(repair)
            if not repair:
                raise ValueError("invalid JSON")
            return ActionEnvelope(kind="finish", reason="fixed", expected_state="CREATED")

    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = RepairAdapter()
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert adapter.repairs == [False, True]
    assert result.attempt.model_calls_used == 2
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
