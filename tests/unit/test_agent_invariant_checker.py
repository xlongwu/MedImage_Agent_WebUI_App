from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_harness import AgentActionRecord, AgentHarnessAttempt
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord, RunLinkRecord
from src.backend.app.services.agent_invariant_checker import AgentInvariantChecker
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _store(tmp_path) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "invariants.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI",
        created_date="today", subjects_count=0, current_pipeline_id="pipeline",
        sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    return store


def _lifecycle(store):
    return AgentOrchestrator(store).create(
        project_id="project-1", command_id="create", actor="researcher", goal_text="Create a plan",
    )


def test_diagnostic_is_redacted_read_only_unless_explicitly_audited(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    checker = AgentInvariantChecker(store)

    report = checker.check(project_id="project-1", lifecycle_id=lifecycle.lifecycle_id)

    assert [finding.code for finding in report.findings] == ["AGENT_INV_WAKE_MISSING"]
    assert store.list_agent_invariant_audits(lifecycle.lifecycle_id) == []

    audited = checker.check(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, persist_audit=True,
    )

    records = store.list_agent_invariant_audits(lifecycle.lifecycle_id)
    assert records[0].report_hash
    assert records[0].finding_codes == tuple(finding.code for finding in audited.findings)
    assert "goal_text" not in records[0].model_dump_json()


def test_plan_missing_input_hash_blocks_before_approval(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    reviewed = store.add_reviewed_plan(ReviewedPlanRecord(
        reviewed_plan_id="plan-1", project_id="project-1", project_config_path="project.yaml",
        plan_hash="plan-hash", created_at="2026-08-16T00:00:00Z", updated_at="2026-08-16T00:00:00Z",
        payload={"plan": {"nodes": []}},
    ))
    lifecycle = AgentOrchestrator(store).transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="CONTEXT_READY",
        command_id="context", actor="researcher", source_command="context_ready",
    )
    lifecycle = AgentOrchestrator(store).transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="PLAN_DRAFTED",
        command_id="draft", actor="researcher", source_command="plan_drafted",
    )
    lifecycle = AgentOrchestrator(store).transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="PLAN_VALIDATED",
        command_id="validated", actor="researcher", source_command="plan_validated",
        updates={"reviewed_plan_id": reviewed.reviewed_plan_id},
    )

    report = AgentInvariantChecker(store).check(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id,
    )

    assert "AGENT_INV_PLAN_WITHOUT_INPUT_HASH" in {finding.code for finding in report.blocking}
    with pytest.raises(SafetyError, match="AGENT_INV_PLAN_WITHOUT_INPUT_HASH"):
        AgentInvariantChecker(store).assert_clear(project_id="project-1", lifecycle_id=lifecycle.lifecycle_id)


def test_action_without_completed_call_and_plan_only_run_are_blocking(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    attempt = store.create_agent_harness_attempt(AgentHarnessAttempt(
        attempt_id="attempt-1", lifecycle_id=lifecycle.lifecycle_id, project_id="project-1",
        provider_ref="rule_based", deadline_at=datetime(2026, 8, 17, tzinfo=UTC),
    ))
    store.add_agent_harness_action(AgentActionRecord(
        action_id="action-1", attempt_id=attempt.attempt_id, step_id="step-1",
        request_hash="missing-call", response_hash="response", action_hash="action-hash",
        kind="draft_plan", expected_state="CREATED", status="accepted",
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    ))
    reviewed = store.add_reviewed_plan(ReviewedPlanRecord(
        reviewed_plan_id="plan-only", project_id="project-1", project_config_path="project.yaml",
        plan_hash="plan-only-hash", planning_inputs_hash="inputs", evidence_snapshot_hash="evidence",
        created_at="2026-08-16T00:00:00Z", updated_at="2026-08-16T00:00:00Z",
        payload={"execution_performed": False, "plan": {"nodes": []}},
    ))
    store.add_run_link(RunLinkRecord(
        run_link_id="run-link-1", project_id="project-1", reviewed_plan_id=reviewed.reviewed_plan_id,
        run_id="run-1", project_config_path="project.yaml", created_at="2026-08-16T00:00:00Z",
        updated_at="2026-08-16T00:00:00Z",
    ))
    lifecycle = AgentOrchestrator(store).transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="CONTEXT_READY",
        command_id="context", actor="researcher", source_command="context_ready",
    )
    lifecycle = AgentOrchestrator(store).transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="PLAN_DRAFTED",
        command_id="draft", actor="researcher", source_command="plan_drafted",
    )
    lifecycle = AgentOrchestrator(store).transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="PLAN_VALIDATED",
        command_id="validated", actor="researcher", source_command="plan_validated",
        updates={"reviewed_plan_id": reviewed.reviewed_plan_id},
    )

    report = AgentInvariantChecker(store).check(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id,
    )

    assert {finding.code for finding in report.blocking} >= {
        "AGENT_INV_ACTION_WITHOUT_CALL",
        "AGENT_INV_RUN_WITHOUT_CONSUMED_TICKET",
        "AGENT_INV_PLAN_ONLY_HAS_EXECUTION",
    }


def test_terminal_lifecycle_requires_observation_artifact_truth(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    reviewed = store.add_reviewed_plan(ReviewedPlanRecord(
        reviewed_plan_id="plan-terminal", project_id="project-1", project_config_path="project.yaml",
        plan_hash="terminal-hash", planning_inputs_hash="inputs", evidence_snapshot_hash="evidence",
        created_at="2026-08-16T00:00:00Z", updated_at="2026-08-16T00:00:00Z",
        payload={"plan": {"nodes": []}},
    ))
    orchestrator = AgentOrchestrator(store)
    for state, command_id in (("CONTEXT_READY", "context"), ("PLAN_DRAFTED", "draft")):
        lifecycle = orchestrator.transition(
            project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state=state,
            command_id=command_id, actor="researcher", source_command=state.lower(),
        )
    lifecycle = orchestrator.transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="PLAN_VALIDATED",
        command_id="validate", actor="researcher", source_command="validated",
        updates={"reviewed_plan_id": reviewed.reviewed_plan_id},
    )
    lifecycle = orchestrator.transition(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id, to_state="SUCCEEDED",
        command_id="finish", actor="researcher", source_command="finished",
    )

    report = AgentInvariantChecker(store).check(
        project_id="project-1", lifecycle_id=lifecycle.lifecycle_id,
    )

    assert "AGENT_INV_COMPLETED_WITHOUT_ARTIFACT_TRUTH" in {
        finding.code for finding in report.blocking
    }
