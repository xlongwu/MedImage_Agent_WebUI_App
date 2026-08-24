from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.project_agent_summary_service import ProjectAgentSummaryService


def test_project_summary_uses_latest_canonical_agent_task(tmp_path: Path) -> None:
    store = SQLiteDesktopStore(tmp_path / "summary.sqlite")
    store.add_project(
        ProjectDetail(
            id="project-1", name="Project", study_id="study", modality="rs-fMRI",
            created_date="2026-08-23", subjects_count=1, current_pipeline_id="legacy-pipeline",
            sequences=[], scans_count=1, total_size="0", current_model_id="", metadata={},
        ),
        health_status="ready",
        rawdata_dir="",
    )
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="task-1", project_id="project-1", state="WAITING_FOR_INPUT",
        goal_text="Generate FC", created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id="event-1", lifecycle_id="task-1", project_id="project-1",
            command_id="create-1", actor="test", source_command="create",
            occurred_at=datetime.now(UTC), from_state=None, to_state="WAITING_FOR_INPUT",
        ),
    )

    projected = next(
        item for item in ProjectAgentSummaryService(store).list_projects()
        if item.id == "project-1"
    )

    assert projected.latest_agent_task is not None
    assert projected.latest_agent_task.task_id == "task-1"
    assert projected.latest_agent_task.state == "waiting_for_user"
    assert projected.latest_agent_task.current_action_code == "waiting_input"
    assert projected.latest_agent_task.requires_user is True
    assert projected.latest_agent_task.result_title is None
    assert projected.latest_agent_task.recent_activity == projected.latest_agent_task.current_action
