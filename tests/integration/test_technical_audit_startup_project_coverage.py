"""Reproduce the startup coverage requirement without touching a user store."""

from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.unit.test_agent_task_scheduler import RecordingPlanningService


def test_startup_rescan_does_not_permanently_hide_project_after_first_batch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MEDIMAGE_DESKTOP_SEED_DEMO_DATA", "false")
    store = SQLiteDesktopStore(tmp_path / "startup-coverage.sqlite")
    for index in range(AgentTaskScheduler.RESCAN_LIMIT + 1):
        store.add_project(
            ProjectDetail(
                id=f"audit-project-{index:03d}", name=f"Fixture {index}",
                study_id="synthetic", modality="rs-fMRI", created_date="fixture",
                subjects_count=0, current_pipeline_id="none", sequences=[],
                scans_count=0, total_size="0", current_model_id="none",
            ),
            health_status="ready", rawdata_dir="",
        )
    projects = store.list_projects()
    assert len(projects) == AgentTaskScheduler.RESCAN_LIMIT + 1
    last_project = projects[-1]
    lifecycle = AgentOrchestrator(store).create(
        project_id=last_project.id, command_id="crash-before-wake",
        actor="test", goal_text="Prepare a plan only",
    )
    assert store.list_agent_task_wakes(project_id=last_project.id) == []
    reopened = SQLiteDesktopStore(tmp_path / "startup-coverage.sqlite")
    planner = RecordingPlanningService()
    scheduler = AgentTaskScheduler(
        reopened, planning_service=planner, start_workers=False
    )
    first = scheduler.rescan()
    second = scheduler.rescan()
    # A bounded scan may yield; repeated scans must eventually reach the tail.
    assert lifecycle.lifecycle_id in set(first) | set(second)
    assert len(reopened.list_agent_task_wakes(project_id=last_project.id)) == 1
    assert planner.calls == []  # rescan itself must not call the planner.
    assert reopened.list_execution_tickets(last_project.id) == []
