from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _store(tmp_path) -> tuple[SQLiteDesktopStore, AgentLifecycleRecord]:
    store = SQLiteDesktopStore(tmp_path / "evidence-context.sqlite")
    store.add_project(ProjectDetail(
        id="project-context", name="context", study_id="study", modality="rs-fMRI",
        created_date="today", subjects_count=1, current_pipeline_id="", sequences=[],
        scans_count=1, total_size="0", current_model_id="", metadata={"agent_planner_provider": "rule_based"},
    ), health_status="ready", rawdata_dir="")
    lifecycle = AgentLifecycleRecord(lifecycle_id="lifecycle-context", project_id="project-context", goal_text="Plan")
    from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent
    store.create_agent_lifecycle(lifecycle, AgentLifecycleEvent(
        event_id="event-context", lifecycle_id=lifecycle.lifecycle_id, project_id=lifecycle.project_id,
        command_id="create", actor="test", source_command="create", occurred_at=datetime.now(UTC),
        from_state=None, to_state="CREATED",
    ))
    return store, lifecycle


def test_context_evidence_is_purpose_scoped_and_registered(tmp_path) -> None:
    store, lifecycle = _store(tmp_path)
    service = AgentEvidenceService(store)
    snapshot = service.build_snapshot(project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id)

    selected = service.read_for_context(
        snapshot_hash=snapshot.snapshot_hash, project_id=lifecycle.project_id,
        lifecycle_id=lifecycle.lifecycle_id, purpose="plan_draft",
    )

    assert selected.snapshot_hash != snapshot.snapshot_hash
    assert set(selected.requested_types) == {"project", "dataset", "artifacts", "plans", "capabilities", "memory"}
    assert {fact.key for fact in selected.facts} <= {
        "dataset_type", "subject_count", "dataset_health", "dataset_subject_count",
        "registered_input_count", "reviewed_plan_count", "planner_provider", "memory_suggestion_count",
    }
    assert all(reference.source_hash for reference in selected.source_refs)


@pytest.mark.parametrize("case, expected", [
    ("missing", "AGENT_CONTEXT_EVIDENCE_MISSING"),
    ("project", "AGENT_CONTEXT_EVIDENCE_PROJECT_MISMATCH"),
    ("hash", "AGENT_CONTEXT_EVIDENCE_HASH_MISMATCH"),
    ("stale", "AGENT_CONTEXT_EVIDENCE_STALE"),
])
def test_context_evidence_rejects_unusable_references(tmp_path, monkeypatch, case, expected) -> None:
    store, lifecycle = _store(tmp_path)
    service = AgentEvidenceService(store)
    snapshot = service.build_snapshot(project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id)
    kwargs = {
        "snapshot_hash": snapshot.snapshot_hash, "project_id": lifecycle.project_id,
        "lifecycle_id": lifecycle.lifecycle_id, "purpose": "decision_request",
    }
    if case == "missing":
        kwargs["snapshot_hash"] = "missing"
    elif case == "project":
        kwargs["project_id"] = "other-project"
    elif case == "hash":
        monkeypatch.setattr(store, "get_agent_evidence_snapshot", lambda _hash: snapshot.model_copy(update={"snapshot_hash": "other"}))
    elif case == "stale":
        monkeypatch.setattr(store, "get_agent_evidence_snapshot", lambda _hash: snapshot.model_copy(update={
            "created_at": datetime.now(UTC) - AgentEvidenceService.MAX_CONTEXT_SNAPSHOT_AGE - timedelta(seconds=1),
        }))

    with pytest.raises(SafetyError) as error:
        service.read_for_context(**kwargs)
    assert error.value.code == expected
