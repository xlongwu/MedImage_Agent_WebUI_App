"""Harness regression coverage retained from the phase-zero characterization.

Each repair phase replaces only its applicable defect assertion with the
required behavior. No xfail, real network, desktop database, or research
data is involved.
"""
from datetime import UTC, datetime, timedelta

import pytest

from src.backend.app.core.config_schema import AgentHarnessConfig, AgentModelRuntimeConfig
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_planning_action_service import (
    AgentPlanningActionConflictError,
    AgentPlanningActionService,
)
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.memory_repository import MemoryRepository
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.integration.test_agent_harness_lifecycle import _service
from tests.unit.test_agent_harness_service import Adapter, _decision, _store
from tests.unit.test_memory_repository import _remember


def test_first_question_answer_uses_the_persisted_evidence_snapshot(tmp_path):
    store = _store(tmp_path)
    harness = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=Adapter(_decision()))
    commands, scheduler = _service(store, harness)
    task = commands.create(project_id="project-1", goal="Plan preprocessing", command_id="create", actor="user")
    scheduler.run_once(owner="baseline")
    task = store.get_agent_lifecycle(task.lifecycle_id)
    assert task.pending_decision_batch.evidence_snapshot_hash == task.evidence_snapshot_hash
    resumed = commands.answer(project_id=task.project_id, lifecycle_id=task.lifecycle_id,
                              batch_id=task.pending_decision_batch.batch_id,
                              answers=[{"item_id": "atlas", "value": "aal"}], command_id="answer", actor="user")
    assert resumed.state == "PLAN_DRAFTED"
    assert store.list_execution_tickets(task.project_id) == []


def test_action_conflict_returns_the_structured_domain_error(tmp_path):
    actions = AgentPlanningActionService(_store(tmp_path), draft_plan=lambda **kw: None)
    with pytest.raises(AgentPlanningActionConflictError) as error:
        actions.apply(lifecycle_id="missing", action=_decision(), actor="user")
    assert error.value.code == "AGENT_HARNESS_ACTION_STATE_CONFLICT"


def test_baseline_future_retry_has_no_worker(tmp_path):
    class FailingPlanner:
        calls = 0

        def advance_planning(self, **kwargs):
            self.calls += 1
            raise ConnectionError("scripted temporary failure")

    planner = FailingPlanner()
    scheduler = AgentTaskScheduler(_store(tmp_path), planning_service=planner, start_workers=False)
    scheduler.enqueue(project_id="project-1", lifecycle_id="task", step_key="create", reason="create")
    scheduler._start_worker()
    worker = scheduler._worker
    if worker is not None:
        worker.join(timeout=5)
        assert not worker.is_alive()
    try:
        wake, = scheduler.store.list_agent_task_wakes(project_id="project-1")
        assert wake.status == "RETRY"
        assert wake.available_at > wake.updated_at
        assert scheduler._worker is None
        assert planner.calls == 1
    finally:
        assert scheduler.shutdown()


def test_baseline_crash_at_provider_boundary_is_misclassified(tmp_path):
    class ProcessLoss(BaseException):
        pass

    class InterruptedProvider:
        calls = 0

        def propose_action(self, **kwargs):
            self.calls += 1
            raise ProcessLoss()

    store = _store(tmp_path)
    task = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user", goal_text="Plan preprocessing")
    clock = [datetime.now(UTC)]
    adapter = InterruptedProvider()
    config = AgentHarnessConfig(enabled=True)
    model = AgentModelRuntimeConfig(provider="openai_compatible")
    harness = AgentHarnessService(store, config=config, model_config=model, adapter=adapter, now=lambda: clock[0])
    harness.ensure_attempt(lifecycle=task, provider_ref="openai_compatible")
    with pytest.raises(ProcessLoss):
        harness.run_one(lifecycle=task, actor="user")
    attempt = store.get_agent_harness_attempt(task.lifecycle_id)
    call, = store.list_agent_harness_steps(attempt.attempt_id)[0].model_calls
    assert call.status == "started" and call.network_called is False
    clock[0] = attempt.lease_expires_at + timedelta(seconds=1)
    reopened = SQLiteDesktopStore(tmp_path / "harness.sqlite")
    restarted = AgentHarnessService(reopened, config=config, model_config=model, adapter=adapter, now=lambda: clock[0])
    result = restarted.run_one(lifecycle=reopened.get_agent_lifecycle(task.lifecycle_id), actor="user")
    assert result.attempt.status == "READY"  # Desired: STOPPED/outcome unknown.
    assert adapter.calls == 1


@pytest.mark.parametrize("state", ["OBSERVING", "EVALUATING"])
def test_baseline_startup_skips_persisted_intermediate_state(tmp_path, state):
    store = _store(tmp_path)
    task = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    # Persist the crash checkpoint through the store's transactional API.
    from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent
    changed = task.model_copy(update={"state": state})
    event = AgentLifecycleEvent(event_id="checkpoint", lifecycle_id=task.lifecycle_id,
        project_id=task.project_id, command_id="checkpoint", actor="test",
        source_command="crash_fixture", occurred_at=datetime.now(UTC), from_state=task.state, to_state=state)
    store.transition_agent_lifecycle(changed, event, expected_state=task.state)
    reopened = SQLiteDesktopStore(tmp_path / "harness.sqlite")
    assert AgentTaskReconciler(reopened).reconcile_incomplete_on_startup() == ()
    assert reopened.get_agent_lifecycle(task.lifecycle_id).state == state


def test_baseline_old_unique_memory_hit_is_lost(tmp_path, monkeypatch):
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    monkeypatch.setattr("src.backend.app.services.memory_repository.utc_iso", lambda *args: "2026-01-01T00:00:00+00:00")
    _remember(repository, key="old-entry", summary="uniqueneedle")
    monkeypatch.setattr("src.backend.app.services.memory_repository.utc_iso", lambda *args: "2026-01-02T00:00:00+00:00")
    for index in range(200):
        _remember(repository, command_id=f"remember-{index:04d}", key=f"entry-{index:04d}", summary="Unrelated preference")
    with repository.connect() as conn:
        assert conn.execute("SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'uniqueneedle'").fetchone()[0] == 1
    assert repository.retrieve_active_items(project_id="project-a", query="uniqueneedle", limit=200) == []


def test_baseline_user_wait_exhausts_work_budget(tmp_path):
    store = _store(tmp_path)
    clock = [datetime.now(UTC)]
    harness = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=Adapter(_decision()), now=lambda: clock[0])
    task = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user", goal_text="Plan preprocessing")
    harness.ensure_attempt(lifecycle=task, provider_ref="rule_based")
    result = harness.run_one(lifecycle=task, actor="user")
    assert result.attempt.status == "WAITING_FOR_USER"
    clock[0] += timedelta(minutes=10)
    resumed = harness.prepare_resume(lifecycle=result.lifecycle, provider_ref="rule_based")
    assert harness._budget_stop_reason(resumed) == "AGENT_HARNESS_WALL_TIME_BUDGET_EXHAUSTED"
