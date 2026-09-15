from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_execution_coordinator import AgentExecutionCoordinator
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _store(tmp_path):
    store = SQLiteDesktopStore(tmp_path / "coordination.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI",
        created_date="today", subjects_count=0, current_pipeline_id="pipeline",
        sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    return store


class _Reconciler:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.calls = []
        self.orchestrator = SimpleNamespace(
            get=lambda **_kwargs: self.lifecycle,
        )

    def reconcile_once(self, **kwargs):
        self.calls.append(kwargs)
        return self.lifecycle


def test_execution_coordinator_consumes_only_after_terminal_lifecycle(tmp_path) -> None:
    lifecycle = SimpleNamespace(
        project_id="project-1", lifecycle_id="lifecycle-1", run_id="run-1",
        state="RUNNING",
    )
    reconciler = _Reconciler(lifecycle)
    coordinator = AgentExecutionCoordinator(
        _store(tmp_path), reconciler=reconciler, start_workers=False
    )
    coordinator.schedule(lifecycle=lifecycle)
    reconciler.lifecycle = lifecycle = SimpleNamespace(**{**lifecycle.__dict__, "state": "GOAL_SATISFIED"})

    result = coordinator.run_once(owner="coordinator-one")

    assert result.state == "GOAL_SATISFIED"
    wakes = coordinator.store.list_agent_execution_wakes(
        project_id="project-1", include_consumed=True
    )
    assert len(wakes) == 1 and wakes[0].status == "CONSUMED"
    assert reconciler.calls == [{"project_id": "project-1", "lifecycle_id": "lifecycle-1"}]


def test_execution_coordinator_persists_the_next_check_for_a_running_original_run(tmp_path) -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    lifecycle = SimpleNamespace(
        project_id="project-1", lifecycle_id="lifecycle-1", run_id="run-1",
        state="RUNNING",
    )
    coordinator = AgentExecutionCoordinator(
        _store(tmp_path), reconciler=_Reconciler(lifecycle), start_workers=False,
        now=lambda: now[0],
    )
    coordinator.schedule(lifecycle=lifecycle)

    result = coordinator.run_once(owner="coordinator-one")

    assert result.state == "RUNNING"
    wake = coordinator.store.list_agent_execution_wakes(project_id="project-1")[0]
    assert wake.status == "RETRY"
    assert wake.available_at == now[0] + timedelta(seconds=coordinator.POLL_INTERVAL_SECONDS)
