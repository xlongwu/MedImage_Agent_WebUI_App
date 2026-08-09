from __future__ import annotations

from pathlib import Path

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _project(tmp_path: Path) -> ProjectDetail:
    return ProjectDetail(
        id="project-evidence", name="Evidence", study_id="study", modality="rs-fMRI",
        created_date="2026-08-09", subjects_count=2, current_pipeline_id="", sequences=[],
        scans_count=2, total_size="0", current_model_id="", metadata={
            "project_dir": str(tmp_path), "agent_planner_provider": "rule_based", "subject_count": 3,
        },
    )


def test_snapshot_is_redacted_stable_and_persisted(tmp_path: Path) -> None:
    store = SQLiteDesktopStore(tmp_path / "state.sqlite")
    store.add_project(_project(tmp_path), health_status="Imported", rawdata_dir=str(tmp_path / "rawdata"))
    lifecycle = AgentLifecycleRecord(lifecycle_id="lifecycle-evidence", project_id="project-evidence")
    # The service checks the canonical lifecycle binding; persist it through its normal store surface.
    from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent
    from datetime import UTC, datetime
    event = AgentLifecycleEvent(
        event_id="event-evidence", lifecycle_id=lifecycle.lifecycle_id, project_id=lifecycle.project_id,
        command_id="create-evidence", actor="test", source_command="create", occurred_at=datetime.now(UTC),
        from_state=None, to_state="CREATED",
    )
    store.create_agent_lifecycle(lifecycle, event)

    first = AgentEvidenceService(store).build_snapshot(
        project_id="project-evidence", lifecycle_id="lifecycle-evidence"
    )
    second = AgentEvidenceService(store).build_snapshot(
        project_id="project-evidence", lifecycle_id="lifecycle-evidence"
    )

    assert first.snapshot_hash == second.snapshot_hash
    assert store.get_agent_evidence_snapshot(first.snapshot_hash) == first
    assert any(item.code == "EVIDENCE_SUBJECT_COUNT_CONFLICT" for item in first.warnings)
    assert str(tmp_path).casefold() not in str(first.model_dump()).casefold()
