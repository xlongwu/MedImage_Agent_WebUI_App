from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_operational_summary_service import (
    AgentOperationalSummaryService,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore


def test_summary_is_empty_and_project_scoped_without_writes(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "operations.sqlite")
    for project_id in ("a", "b"):
        store.add_project(ProjectDetail(
            id=project_id, name=project_id, study_id="synthetic", modality="rs-fMRI",
            created_date="today", subjects_count=0, current_pipeline_id="pipeline",
            sequences=[], scans_count=0, total_size="0", current_model_id="none",
        ), health_status="ready", rawdata_dir="")
    summary = AgentOperationalSummaryService(store).build(project_id="a")
    assert summary.project_id == "a"
    assert summary.schema_version == 2
    assert summary.task_counts == {"total": 0}
    assert summary.model_call_counts == {}
    assert summary.team_model_call_counts == {}
    assert summary.team_status_counts == {}
    assert summary.provider_failure_counts == {}
    assert summary.scheduler_counts == {}
    assert summary.approval_counts == {"approved": 0, "waiting": 0}
    assert summary.gateway_counts == {}
    assert summary.sandbox_counts == {}
    assert summary.memory_status == "unavailable"
    assert summary.attention == ()


def test_summary_reads_persisted_state_without_mutating_the_store(tmp_path) -> None:
    db_path = tmp_path / "operations.sqlite"
    store = SQLiteDesktopStore(db_path)
    store.add_project(ProjectDetail(
        id="project", name="project", study_id="synthetic", modality="rs-fMRI",
        created_date="today", subjects_count=0, current_pipeline_id="pipeline",
        sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    before = sha256(db_path.read_bytes()).hexdigest()

    summary = AgentOperationalSummaryService(store).build(project_id="project")

    after = sha256(db_path.read_bytes()).hexdigest()
    assert before == after
    assert summary.task_counts["total"] == 0


def test_summary_includes_team_statuses_calls_and_safe_attention(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "operations-team.sqlite")
    project = ProjectDetail(
        id="project-team", name="project-team", study_id="synthetic", modality="rs-fMRI",
        created_date="today", subjects_count=0, current_pipeline_id="pipeline",
        sequences=[], scans_count=0, total_size="0", current_model_id="none",
    )
    store.add_project(project, health_status="ready", rawdata_dir="")
    now = datetime.now(UTC)
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="task-team",
        project_id=project.id,
        state="CONTEXT_READY",
        planning_mode="multi_agent",
        planning_team_id="team-1",
        planning_team_epoch=1,
        created_at=now,
        updated_at=now,
    )
    store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id="event-team",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=project.id,
            command_id="command-team",
            actor="test",
            source_command="create",
            occurred_at=now,
            from_state=None,
            to_state="CONTEXT_READY",
        ),
    )
    team = SimpleNamespace(
        project_id=project.id,
        lifecycle_id=lifecycle.lifecycle_id,
        team_id="team-1",
        team_epoch=1,
        status="NEEDS_ATTENTION",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    calls = [
        SimpleNamespace(
            project_id=project.id,
            lifecycle_id=lifecycle.lifecycle_id,
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            model_call_id="call-completed",
            status="completed",
            network_called=True,
            latency_ms=50,
            error_code=None,
        ),
        SimpleNamespace(
            project_id=project.id,
            lifecycle_id=lifecycle.lifecycle_id,
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            model_call_id="call-failed",
            status="failed",
            network_called=True,
            latency_ms=75,
            error_code="AGENT_MODEL_PROVIDER_FAILED",
        ),
        SimpleNamespace(
            project_id=project.id,
            lifecycle_id=lifecycle.lifecycle_id,
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            model_call_id="call-started",
            status="started",
            network_called=True,
            latency_ms=None,
            error_code=None,
        ),
    ]
    store.list_agent_teams = lambda _project_id: [team]
    store.list_agent_team_model_calls = lambda _project_id, _team_id: calls

    summary = AgentOperationalSummaryService(store).build(project_id=project.id)

    assert summary.team_status_counts == {"NEEDS_ATTENTION": 1}
    assert summary.team_model_call_counts == {
        "failure": 1,
        "success": 1,
        "total": 3,
        "unknown": 1,
    }
    assert summary.model_call_counts == summary.team_model_call_counts
    assert summary.provider_failure_counts == {"AGENT_MODEL_PROVIDER_FAILED": 1}
    assert {item.code for item in summary.attention} == {
        "AGENT_OP_TEAM_NEEDS_ATTENTION",
        "AGENT_OP_TEAM_PROVIDER_FAILURES",
        "AGENT_OP_TEAM_UNKNOWN_MODEL_CALL",
    }


class _BrokenMemoryRepository:
    def health_check(self):
        return {"ok": False, "error_code": "MEMORY_STORE_UNHEALTHY"}


def test_enabled_unhealthy_memory_is_attention_only(tmp_path) -> None:
    from src.backend.app.core.config_schema import MemoryConfig

    store = SQLiteDesktopStore(tmp_path / "operations.sqlite")
    project_id = store.list_projects()[0].id
    store.set_memory_consent(
        project_id=project_id,
        command_id="operations-memory-consent",
        principal="test",
        generate_enabled=True,
        use_enabled=True,
    )
    config = MemoryConfig(
        enabled=True,
        generation_enabled=True,
        use_enabled=True,
        store_path=str(tmp_path / "missing-memory.sqlite"),
    )

    summary = AgentOperationalSummaryService(
        store,
        memory_repository=_BrokenMemoryRepository(),
        memory_config=config,
    ).build(project_id=project_id)

    assert summary.memory_status == "failure"
    assert [(item.code, item.severity) for item in summary.attention] == [
        ("AGENT_OP_MEMORY_UNAVAILABLE", "warning")
    ]


def test_summary_truncates_at_500_and_never_mixes_projects(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "operations.sqlite")
    now = datetime.now(UTC)
    for project_id in ("project-a", "project-b"):
        store.add_project(ProjectDetail(
            id=project_id, name=project_id, study_id="synthetic", modality="rs-fMRI",
            created_date="today", subjects_count=0, current_pipeline_id="pipeline",
            sequences=[], scans_count=0, total_size="0", current_model_id="none",
        ), health_status="ready", rawdata_dir="")

    for index in range(501):
        lifecycle_id = f"a-{index:03d}"
        record = AgentLifecycleRecord(
            lifecycle_id=lifecycle_id,
            project_id="project-a",
            state="CREATED",
            created_at=now,
            updated_at=now,
        )
        store.create_agent_lifecycle(record, AgentLifecycleEvent(
            event_id=f"event-{lifecycle_id}",
            lifecycle_id=lifecycle_id,
            project_id="project-a",
            command_id=f"command-{lifecycle_id}",
            actor="test",
            source_command="create",
            occurred_at=now,
            from_state=None,
            to_state="CREATED",
        ))

    foreign = AgentLifecycleRecord(
        lifecycle_id="b-only",
        project_id="project-b",
        state="FAILED",
        created_at=now,
        updated_at=now,
    )
    store.create_agent_lifecycle(foreign, AgentLifecycleEvent(
        event_id="event-b-only",
        lifecycle_id="b-only",
        project_id="project-b",
        command_id="command-b-only",
        actor="test",
        source_command="create",
        occurred_at=now,
        from_state=None,
        to_state="FAILED",
    ))

    summary = AgentOperationalSummaryService(store).build(project_id="project-a")

    assert summary.truncated is True
    assert summary.task_counts == {"CREATED": 500, "total": 500}
    assert "FAILED" not in summary.task_counts
