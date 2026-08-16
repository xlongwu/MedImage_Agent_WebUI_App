from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.backend.app.main import create_app
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler


class ReconcileStore:
    def __init__(self, *, status="COMPLETED", payload=None) -> None:
        self.lifecycle = AgentLifecycleRecord(
            lifecycle_id="task-1",
            project_id="project-1",
            state="RUNNING",
            reviewed_plan_id="plan-1",
            execution_ticket_id="ticket-1",
            run_id="run-1",
        )
        self.link = SimpleNamespace(status=status, payload=payload or {})

    def get_agent_lifecycle(self, lifecycle_id):
        return self.lifecycle if lifecycle_id == self.lifecycle.lifecycle_id else None

    def get_run_link_by_run_id(self, project_id, run_id):
        return self.link

    def get_observation(self, observation_id):
        return SimpleNamespace(observation_hash="observation-hash") if observation_id == "observation-1" else None

    def get_goal_evaluation(self, evaluation_id):
        return SimpleNamespace(goal_evaluation_hash="evaluation-hash") if evaluation_id == "evaluation-1" else None

    def get_recovery_proposal(self, proposal_id):
        return SimpleNamespace(recovery_proposal_hash="recovery-hash") if proposal_id == "proposal-1" else None


class FakeOrchestrator:
    def __init__(self, store, *, evaluation_status="satisfied") -> None:
        self.store = store
        self.calls = []
        self.evaluation_status = evaluation_status

    def get(self, *, project_id, lifecycle_id):
        return self.store.lifecycle

    def transition(self, *, to_state, **kwargs):
        self.calls.append(to_state)
        self.store.lifecycle = self.store.lifecycle.model_copy(update={"state": to_state})
        return self.store.lifecycle

    def observe(self, **kwargs):
        self.calls.append("OBSERVING")
        self.store.lifecycle = self.store.lifecycle.model_copy(
            update={"state": "OBSERVING", "observation_id": "observation-1"}
        )
        return self.store.lifecycle

    def evaluate_goal(self, **kwargs):
        self.calls.extend(
            [
                "EVALUATING",
                "GOAL_SATISFIED" if self.evaluation_status == "satisfied" else "DIAGNOSING",
            ]
        )
        target = "GOAL_SATISFIED" if self.evaluation_status == "satisfied" else "DIAGNOSING"
        self.store.lifecycle = self.store.lifecycle.model_copy(
            update={"state": target, "goal_evaluation_id": "evaluation-1"}
        )
        return self.store.lifecycle, SimpleNamespace(
            status=self.evaluation_status,
            goal_evaluation_hash="evaluation-hash",
        )

    def propose_recovery(self, **kwargs):
        self.calls.append("RECOVERY_PROPOSED")
        self.store.lifecycle = self.store.lifecycle.model_copy(
            update={"state": "RECOVERY_PROPOSED", "recovery_proposal_id": "proposal-1"}
        )
        return self.store.lifecycle, object(), object()


def test_reconcile_success_is_bounded_and_idempotent() -> None:
    store = ReconcileStore()
    reconciler = AgentTaskReconciler(store)
    reconciler.orchestrator = FakeOrchestrator(store)

    result = reconciler.reconcile_once(project_id="project-1", lifecycle_id="task-1")
    again = reconciler.reconcile_once(project_id="project-1", lifecycle_id="task-1")

    assert result.state == "GOAL_SATISFIED"
    assert again.state == "GOAL_SATISFIED"
    assert reconciler.orchestrator.calls == ["OBSERVING", "EVALUATING", "GOAL_SATISFIED"]
    assert len(reconciler.orchestrator.calls) <= reconciler.MAX_TRANSITIONS


def test_reconcile_wakes_harness_only_after_terminal_records_are_persisted() -> None:
    store = ReconcileStore()
    wakes = []
    reconciler = AgentTaskReconciler(
        store,
        planning_waker=lambda *, lifecycle, reason, details: wakes.append((lifecycle.state, reason, details)) or True,
    )
    reconciler.orchestrator = FakeOrchestrator(store)

    result = reconciler.reconcile_once(project_id="project-1", lifecycle_id="task-1")

    assert result.state == "GOAL_SATISFIED"
    assert wakes[0][:2] == ("GOAL_SATISFIED", "run_reconciled")
    assert wakes[0][2]["lifecycle_id"] == "task-1"
    assert wakes[0][2]["run_id"] == "run-1"
    assert wakes[0][2]["evaluation_hash"] == "evaluation-hash"
    assert wakes[0][2]["wake_hash"]


def test_reconcile_failed_goal_stops_at_unapproved_recovery_proposal() -> None:
    store = ReconcileStore(status="FAILED")
    reconciler = AgentTaskReconciler(store)
    reconciler.orchestrator = FakeOrchestrator(store, evaluation_status="not_satisfied")

    result = reconciler.reconcile_once(project_id="project-1", lifecycle_id="task-1")

    assert result.state == "RECOVERY_PROPOSED"
    assert reconciler.orchestrator.calls[-1] == "RECOVERY_PROPOSED"
    assert "WAITING_FOR_RECOVERY_APPROVAL" not in reconciler.orchestrator.calls
    assert len(reconciler.orchestrator.calls) == reconciler.MAX_TRANSITIONS


def test_conflicting_terminal_evidence_hands_off_without_observation() -> None:
    store = ReconcileStore(payload={"terminal_conflict": True})
    wakes = []
    reconciler = AgentTaskReconciler(
        store,
        planning_waker=lambda *, lifecycle, reason, details: wakes.append((lifecycle.state, reason, details)) or True,
    )
    reconciler.orchestrator = FakeOrchestrator(store)

    result = reconciler.reconcile_once(project_id="project-1", lifecycle_id="task-1")

    assert result.state == "HUMAN_HANDOFF"
    assert reconciler.orchestrator.calls == ["HUMAN_HANDOFF"]
    assert wakes[0][:2] == ("HUMAN_HANDOFF", "run_reconciled")
    assert wakes[0][2]["wake_hash"]


def test_concurrent_reconcile_creates_one_observation_evaluation_chain() -> None:
    store = ReconcileStore()
    reconciler = AgentTaskReconciler(store)
    reconciler.orchestrator = FakeOrchestrator(store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: reconciler.reconcile_once(
                    project_id="project-1", lifecycle_id="task-1"
                ),
                range(2),
            )
        )

    assert {result.state for result in results} == {"GOAL_SATISFIED"}
    assert reconciler.orchestrator.calls.count("OBSERVING") == 1
    assert reconciler.orchestrator.calls.count("EVALUATING") == 1


def test_monitor_is_fixed_count_and_stops_after_terminal_evidence() -> None:
    store = ReconcileStore(status="RUNNING")
    reconciler = AgentTaskReconciler(store)
    reconciler.orchestrator = FakeOrchestrator(store)
    checks = []

    original = reconciler.reconcile_once

    def reconcile(**kwargs):
        checks.append(len(checks) + 1)
        if len(checks) == 2:
            store.link.status = "COMPLETED"
        return original(**kwargs)

    reconciler.reconcile_once = reconcile
    result = reconciler.monitor_bounded(
        project_id="project-1",
        lifecycle_id="task-1",
        max_checks=3,
        interval_seconds=0,
        waiter=lambda _seconds: None,
    )

    assert result.state == "GOAL_SATISFIED"
    assert checks == [1, 2]


def test_startup_reconciliation_is_disabled_by_default_and_runs_once_when_enabled(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.delenv("MEDIMAGE_AGENT_STARTUP_RECONCILE", raising=False)
    monkeypatch.setattr(
        AgentTaskReconciler,
        "reconcile_incomplete_on_startup",
        lambda self: calls.append("run") or (),
    )
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code in {200, 404}
    assert calls == []

    monkeypatch.setenv("MEDIMAGE_AGENT_STARTUP_RECONCILE", "1")
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code in {200, 404}
    assert calls == ["run"]
