from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord
from src.backend.app.services.approval_summary_service import ApprovalSummaryService

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _project(tmp_path) -> ProjectDetail:
    return ProjectDetail(
        id="project-1",
        name="Study",
        study_id="study-1",
        modality="rs-fMRI",
        created_date="2026-07-16",
        subjects_count=2,
        current_pipeline_id="",
        sequences=[],
        scans_count=2,
        total_size="1 MB",
        current_model_id="",
        metadata={"project_dir": str(tmp_path)},
    )


def _reviewed(tmp_path, *, output_dir="derivatives/fc") -> ReviewedPlanRecord:
    return ReviewedPlanRecord(
        reviewed_plan_id="reviewed-1",
        project_id="project-1",
        project_config_path=str(tmp_path / "project.yaml"),
        plan_hash="plan-hash",
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        payload={
            "goal": "Compute functional connectivity",
            "goal_contract": {"goal_contract_hash": "goal-contract-hash"},
            "plan": {
                "nodes": [
                    {
                        "id": "functional_connectivity_subject",
                        "backend": "python-cpu",
                        "params": {"output_dir": output_dir},
                    }
                ]
            },
        },
    )


def _native_reviewed(tmp_path, *, subject_id: str) -> ReviewedPlanRecord:
    reviewed = _reviewed(tmp_path)
    reviewed.payload["goal"] = f"Preprocess {subject_id}"
    reviewed.payload["plan"] = {
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "backend": "native_python",
                "params": {
                    "subject_id": subject_id,
                    "input_bids_dir": str(tmp_path / "rawdata"),
                    "confirmations": {},
                    "stage_overrides": {"realignment": True},
                },
            }
        ]
    }
    return reviewed


def test_summary_hash_is_stable_and_expands_granular_confirmations(tmp_path) -> None:
    service = ApprovalSummaryService()
    first = service.build(project=_project(tmp_path), reviewed_plan=_reviewed(tmp_path), now=NOW)
    second = service.build(project=_project(tmp_path), reviewed_plan=_reviewed(tmp_path), now=NOW)

    assert first.summary_hash == second.summary_hash
    assert first.write_roots == (
        "project://derivatives",
        "project://derivatives/fc",
        "project://logs",
        "project://reports",
        "project://work",
    )
    assert first.confirmations["approved_nodes"] == ["functional_connectivity_subject"]
    assert first.rawdata_read_only is True
    service.verify(first, now=NOW + timedelta(minutes=1))


def test_summary_binds_selected_subject_and_changes_hash_with_scope(tmp_path) -> None:
    service = ApprovalSummaryService()
    first = service.build(
        project=_project(tmp_path),
        reviewed_plan=_native_reviewed(tmp_path, subject_id="sub-001"),
        now=NOW,
    )
    changed = service.build(
        project=_project(tmp_path),
        reviewed_plan=_native_reviewed(tmp_path, subject_id="sub-002"),
        now=NOW,
    )

    assert first.dataset_summary == "1 selected subject: sub-001"
    assert first.sections[0].summary == (
        "Approve exactly 1 reviewed node(s) for subject sub-001."
    )
    assert first.summary_hash != changed.summary_hash


def test_summary_hash_and_scope_change_when_write_root_changes(tmp_path) -> None:
    service = ApprovalSummaryService()
    first = service.build(project=_project(tmp_path), reviewed_plan=_reviewed(tmp_path), now=NOW)
    changed = service.build(
        project=_project(tmp_path),
        reviewed_plan=_reviewed(tmp_path, output_dir="derivatives/fc-v2"),
        now=NOW,
    )
    assert changed.summary_hash != first.summary_hash
    assert changed.write_roots != first.write_roots


def test_summary_identity_binds_planning_inputs_and_revision_lineage(tmp_path) -> None:
    service = ApprovalSummaryService()
    reviewed = _reviewed(tmp_path).model_copy(
        update={
            "planning_inputs_hash": "inputs-v1",
            "revision_no": 2,
            "parent_reviewed_plan_id": "reviewed-parent",
            "parent_plan_hash": "parent-hash",
            "revision_reason": "decision_answered",
        }
    )
    first = service.build(project=_project(tmp_path), reviewed_plan=reviewed, now=NOW)
    changed = service.build(
        project=_project(tmp_path),
        reviewed_plan=reviewed.model_copy(update={"planning_inputs_hash": "inputs-v2"}),
        now=NOW,
    )

    assert first.planning_inputs_hash == "inputs-v1"
    assert first.revision_no == 2
    assert first.parent_reviewed_plan_id == "reviewed-parent"
    assert first.revision_reason == "decision_answered"
    assert changed.summary_hash != first.summary_hash


def test_summary_includes_runtime_roots_when_inspection_has_explicit_data_output(tmp_path) -> None:
    reviewed = _reviewed(tmp_path, output_dir="data")
    reviewed.payload["plan"]["nodes"].append(
        {
            "id": "alff_falff_subject",
            "backend": "python-cpu",
            "params": {},
        }
    )

    summary = ApprovalSummaryService().build(
        project=_project(tmp_path), reviewed_plan=reviewed, now=NOW
    )

    assert "project://data" in summary.write_roots
    assert "project://derivatives" in summary.write_roots
    assert "project://work" in summary.write_roots
    assert "project://logs" in summary.write_roots
    assert "project://reports" in summary.write_roots


def test_summary_rejects_outside_project_and_expiry(tmp_path) -> None:
    service = ApprovalSummaryService()
    with pytest.raises(SafetyError, match="APPROVAL_WRITE_ROOT_OUTSIDE_PROJECT"):
        service.build(
            project=_project(tmp_path),
            reviewed_plan=_reviewed(tmp_path, output_dir=str(tmp_path.parent / "escape")),
            now=NOW,
        )

    summary = service.build(project=_project(tmp_path), reviewed_plan=_reviewed(tmp_path), now=NOW)
    with pytest.raises(SafetyError, match="APPROVAL_SUMMARY_EXPIRED"):
        service.verify(summary, now=NOW + timedelta(hours=1))


def test_summary_binds_sanitized_memory_context_snapshot(tmp_path) -> None:
    service = ApprovalSummaryService()
    reviewed = _reviewed(tmp_path)
    reviewed = reviewed.model_copy(
        update={
            "memory_context_hash": "context-hash-1",
            "payload": {
                **reviewed.payload,
                "memory_context": {
                    "decision_suggestions": [
                        {"decision_kind": "atlas", "typed_value": {"value": "secret-not-rendered"}}
                    ],
                    "evidence_refs": [
                        {
                            "kind": "workflow_lesson",
                            "memory_id": "memory-1",
                            "revision_hash": "revision-1",
                            "source_ref": "observation:1",
                            "provenance_warning": None,
                        }
                    ],
                },
            },
        }
    )
    first = service.build(project=_project(tmp_path), reviewed_plan=reviewed, now=NOW)
    assert first.memory_context_hash == "context-hash-1"
    assert first.memory_refs[0]["memory_id"] == "memory-1"
    assert "secret-not-rendered" not in str(first.memory_influence_summary)

    changed = service.build(
        project=_project(tmp_path),
        reviewed_plan=reviewed.model_copy(update={"memory_context_hash": "context-hash-2"}),
        now=NOW,
    )
    assert changed.summary_hash != first.summary_hash
