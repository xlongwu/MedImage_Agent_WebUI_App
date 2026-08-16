from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.mock_store import SQLiteDesktopStore


class RecordingPlanningService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    def advance_planning(self, *, project_id: str, lifecycle_id: str, wake_reason: str) -> object:
        self.calls.append((project_id, lifecycle_id, wake_reason))
        if self.fail:
            raise RuntimeError("planned failure")
        return object()


def _store(tmp_path):
    store = SQLiteDesktopStore(tmp_path / "scheduler.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI",
        created_date="today", subjects_count=0, current_pipeline_id="pipeline",
        sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    return store


def test_scheduler_deduplicates_persisted_wake_and_advances_once(tmp_path) -> None:
    service = RecordingPlanningService()
    scheduler = AgentTaskScheduler(_store(tmp_path), planning_service=service, start_workers=False)

    first = scheduler.enqueue(
        project_id="project-1", lifecycle_id="lifecycle-1", step_key="CREATED:1", reason="create"
    )
    second = scheduler.enqueue(
        project_id="project-1", lifecycle_id="lifecycle-1", step_key="CREATED:1", reason="duplicate"
    )

    assert first.wake_id == second.wake_id
    assert scheduler.run_once(owner="one") == "lifecycle-1"
    assert scheduler.run_once(owner="two") is None
    assert service.calls == [("project-1", "lifecycle-1", "duplicate")]
    wakes = scheduler.store.list_agent_task_wakes(project_id="project-1", include_consumed=True)
    assert len(wakes) == 1 and wakes[0].status == "CONSUMED"


def test_scheduler_failure_keeps_a_retryable_durable_checkpoint(tmp_path) -> None:
    service = RecordingPlanningService(fail=True)
    scheduler = AgentTaskScheduler(_store(tmp_path), planning_service=service, start_workers=False)
    scheduler.enqueue(project_id="project-1", lifecycle_id="lifecycle-1", step_key="CREATED:1", reason="create")

    assert scheduler.run_once(owner="one") == "lifecycle-1"
    wakes = scheduler.store.list_agent_task_wakes(project_id="project-1")
    assert len(wakes) == 1
    assert wakes[0].status == "RETRY"
    assert wakes[0].attempts == 1
    assert wakes[0].last_error_code == "RuntimeError"


def test_only_one_scheduler_can_claim_the_same_persisted_wake(tmp_path) -> None:
    store = _store(tmp_path)
    one = AgentTaskScheduler(store, planning_service=RecordingPlanningService(), start_workers=False)
    two = AgentTaskScheduler(store, planning_service=RecordingPlanningService(), start_workers=False)
    one.enqueue(project_id="project-1", lifecycle_id="lifecycle-1", step_key="CREATED:1", reason="create")

    assert one.claim_next(owner="scheduler-one") is not None
    assert two.claim_next(owner="scheduler-two") is None


def test_lifecycle_creation_commits_its_durable_wake_in_the_same_store(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = AgentOrchestrator(store).create(
        project_id="project-1", command_id="create-1", actor="researcher",
        planning_wake_reason="create",
    )

    wakes = store.list_agent_task_wakes(project_id="project-1")

    assert [(wake.lifecycle_id, wake.reason, wake.step_key) for wake in wakes] == [
        (lifecycle.lifecycle_id, "create", f"CREATED:{lifecycle.updated_at.isoformat()}")
    ]


def test_expired_wake_lease_is_claimable_by_a_restarted_scheduler(tmp_path) -> None:
    store = _store(tmp_path)
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    scheduler = AgentTaskScheduler(
        store,
        planning_service=RecordingPlanningService(),
        start_workers=False,
        now=lambda: clock[0],
    )
    scheduler.enqueue(project_id="project-1", lifecycle_id="lifecycle-1", step_key="CREATED:1", reason="create")
    first = scheduler.claim_next(owner="stalled")
    assert first is not None
    clock[0] = first.lease_expires_at + timedelta(seconds=1)
    claimed = scheduler.claim_next(owner="restarted")
    assert claimed is not None and claimed.lease_owner == "restarted"
