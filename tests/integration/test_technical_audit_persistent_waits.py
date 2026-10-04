"""Controlled-clock restart contracts; no real 10/20-minute runtime claim."""

from datetime import UTC, datetime, timedelta

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent
from src.backend.app.services.agent_execution_coordinator import AgentExecutionCoordinator
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.unit.test_agent_execution_coordinator import _Reconciler
from tests.unit.test_agent_harness_service import Adapter, _decision, _store


def test_ten_minute_user_wait_survives_store_restart_without_budget_charge(tmp_path):
    store = _store(tmp_path)
    clock = [datetime.now(UTC)]
    adapter = Adapter(_decision())
    config = AgentHarnessConfig(enabled=True)
    harness = AgentHarnessService(
        store, config=config, adapter=adapter, now=lambda: clock[0]
    )
    task = AgentOrchestrator(store).create(
        project_id="project-1", command_id="wait-create", actor="user",
        goal_text="Plan preprocessing",
    )
    harness.ensure_attempt(lifecycle=task, provider_ref="rule_based")
    result = harness.run_one(lifecycle=task, actor="user")
    assert result.attempt.status == "WAITING_FOR_USER"
    used = result.attempt.active_seconds_used
    clock[0] += timedelta(minutes=10)
    reopened = SQLiteDesktopStore(tmp_path / "harness.sqlite")
    restarted = AgentHarnessService(
        reopened, config=config, adapter=adapter, now=lambda: clock[0]
    )
    persisted = reopened.get_agent_lifecycle(task.lifecycle_id)
    assert persisted.pending_decision_batch == result.lifecycle.pending_decision_batch
    resumed = restarted.prepare_resume(lifecycle=persisted, provider_ref="rule_based")
    assert resumed.attempt_id == result.attempt.attempt_id
    assert resumed.active_seconds_used == used
    assert restarted._budget_stop_reason(resumed) is None
    assert adapter.calls == 1
    assert reopened.list_execution_tickets(task.project_id) == []


def test_twenty_minute_original_run_keeps_one_durable_check_after_restart(tmp_path):
    store = _store(tmp_path)
    clock = [datetime.now(UTC)]
    original = AgentOrchestrator(store).create(
        project_id="project-1", command_id="run-create", actor="user"
    )
    # A persisted running checkpoint, not an assertion that a runner executed.
    running = original.model_copy(update={"state": "RUNNING", "run_id": "audit-run"})
    store.transition_agent_lifecycle(
        running,
        AgentLifecycleEvent(
            event_id="audit-running-checkpoint", lifecycle_id=running.lifecycle_id,
            project_id=running.project_id, command_id="audit-running-checkpoint",
            actor="test", source_command="isolated_running_fixture",
            occurred_at=clock[0], from_state=original.state, to_state="RUNNING",
        ),
        expected_state=original.state,
    )
    coordinator = AgentExecutionCoordinator(
        store, reconciler=_Reconciler(running), start_workers=False,
        now=lambda: clock[0],
    )
    first = coordinator.schedule(lifecycle=running)
    assert coordinator.run_once(owner="before-restart").run_id == running.run_id
    assert coordinator.shutdown()
    clock[0] += timedelta(minutes=20)
    reopened = SQLiteDesktopStore(tmp_path / "harness.sqlite")
    persisted = reopened.get_agent_lifecycle(running.lifecycle_id)
    reconciler = _Reconciler(persisted)
    restarted = AgentExecutionCoordinator(
        reopened, reconciler=reconciler, start_workers=False,
        now=lambda: clock[0],
    )
    assert restarted.recover_on_startup() == (running.lifecycle_id,)
    assert restarted.recover_on_startup() == (running.lifecycle_id,)
    wakes = reopened.list_agent_execution_wakes(project_id=running.project_id)
    assert len(wakes) == 1 and wakes[0].wake_id == first.wake_id
    assert restarted.run_once(owner="after-restart").run_id == running.run_id
    wake, = reopened.list_agent_execution_wakes(project_id=running.project_id)
    assert wake.status == "RETRY" and wake.run_id == running.run_id
    assert wake.available_at == clock[0] + timedelta(seconds=1)
    assert reconciler.calls == [{"project_id": running.project_id, "lifecycle_id": running.lifecycle_id}]
    assert reopened.list_execution_tickets(running.project_id) == []
    assert restarted.shutdown()
