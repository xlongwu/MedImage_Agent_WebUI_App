from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_operational_summary_service import AgentOperationalSummaryService
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
    assert summary.schema_version == 1
    assert summary.task_counts == {"total": 0}
    assert summary.model_call_counts == {}
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
