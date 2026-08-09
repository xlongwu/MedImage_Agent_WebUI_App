from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.main import app
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord
from src.backend.app.schemas.execution_ticket import ExecutionRetryPolicy, ExecutionTicket
from src.backend.app.schemas.goal_contract import CriterionResult, GoalEvaluationRecord
from src.backend.app.schemas.observation import (
    CapabilityObservation,
    NodeObservation,
    ObservationBindings,
    ObservationCompleteness,
    ObservationRecord,
    ObservationSourceRef,
    PipelineObservation,
    ScientificObservation,
)
from src.backend.app.schemas.recovery import RecoveryBindings
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.observation_collector import calculate_observation_hash
from src.backend.app.services.run_diagnosis_service import (
    RunDiagnosisService,
    adapt_legacy_diagnosis,
    calculate_diagnosis_hash,
)

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def _ticket() -> ExecutionTicket:
    return ExecutionTicket(
        execution_ticket_id="ticket-1",
        project_id="project-1",
        reviewed_plan_id="reviewed-1",
        plan_hash="plan-hash",
        goal_contract_hash="goal-hash",
        evaluation_policy_version="goal-evaluator-v1",
        approval_summary_hash="approval-1",
        approved_actor="reviewer",
        approved_node_ids=("functional_connectivity_subject",),
        approved_backend_ids=("python",),
        input_roots=("project/inputs",),
        output_roots=("project/derivatives",),
        readonly_roots=("project/rawdata",),
        project_config_path="project/project.yaml",
        pipeline_path="project/pipeline.yaml",
        scope_hash="scope-1",
        allowlist_hash="allowlist-1",
        normalized_params_hash=stable_hash({"roi_count": 4}),
        contract_versions=(("functional_connectivity_subject", "1.0.0"),),
        audit_id="audit-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        retry_policy=ExecutionRetryPolicy(
            max_retry_count=2,
            allowed_node_ids=("functional_connectivity_subject",),
            max_lifecycle_recovery_attempts=2,
            max_node_attempts=1,
            max_subject_node_attempts=1,
            max_replans=1,
            max_recovery_wall_seconds=600,
        ),
        canonical_hash="ticket-hash",
    )


def _observation(*, conflict: bool = False) -> ObservationRecord:
    record = ObservationRecord(
        observation_id="observation-1",
        bindings=ObservationBindings(
            project_id="project-1",
            lifecycle_id="lifecycle-1",
            reviewed_plan_id="reviewed-1",
            plan_hash="plan-hash",
            goal_contract_id="goal-1",
            goal_contract_hash="goal-hash",
            run_id="run-1",
            execution_ticket_id="ticket-1",
            dispatch_id="dispatch-1",
        ),
        collected_at=NOW,
        sources=(
            ObservationSourceRef(
                source_id="node-source",
                source_type="node_state",
                read_status="ok",
                observed_at=NOW,
                freshness="fresh",
            ),
        ),
        pipeline=PipelineObservation(
            status="FAILED",
            nodes_total=2,
            nodes_succeeded=1,
            nodes_failed=1,
            active_nodes=0,
            summary_consistent=not conflict,
            evidence_ids=("node-source",),
        ),
        nodes=(
            NodeObservation(
                node_id="functional_connectivity_subject",
                subject_id="sub-01",
                status="SUCCESS",
                attempt=0,
                evidence_ids=("node-source",),
            ),
            NodeObservation(
                node_id="functional_connectivity_subject",
                subject_id="sub-02",
                status="FAILED",
                attempt=0,
                errors=("NODE_FAILED: temporary read failure",),
                evidence_ids=("node-source",),
            ),
        ),
        capability=CapabilityObservation(
            declared_level="computed",
            observed_level="metadata_only",
            defensible_level="metadata_only",
            evidence_ids=("node-source",),
        ),
        scientific=ScientificObservation(status="metadata_only"),
        completeness=ObservationCompleteness(
            status="invalid" if conflict else "complete",
            conflicts=("PIPELINE_NODE_STATE_COUNT_CONFLICT",) if conflict else (),
        ),
        observation_hash="pending",
    )
    return record.model_copy(update={"observation_hash": calculate_observation_hash(record)})


def _evaluation(observation: ObservationRecord) -> GoalEvaluationRecord:
    record = GoalEvaluationRecord(
        goal_evaluation_id="evaluation-1",
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        reviewed_plan_id="reviewed-1",
        plan_hash="plan-hash",
        goal_contract_id="goal-1",
        goal_contract_hash="goal-hash",
        observation_id=observation.observation_id,
        observation_hash=observation.observation_hash,
        evaluated_at=NOW,
        criterion_results=(
            CriterionResult(
                criterion_id="fc-present",
                criterion_type="artifact_present",
                status="failed",
                expected={"artifact_type": "fc_matrix"},
                actual={"count": 1, "passed": 1, "failed": 1},
                affected_subjects=("sub-02",),
                blocking=True,
                reason_code="ARTIFACT_MISSING",
            ),
        ),
        status="not_satisfied",
        goal_evaluation_hash="evaluation-hash",
    )
    return record


def test_diagnosis_binds_evidence_and_extracts_known_failed_subject():
    observation = _observation()
    diagnosis = RunDiagnosisService(get_node_contract).build(
        observation=observation,
        evaluation=_evaluation(observation),
        ticket=_ticket(),
        created_at=NOW,
    )
    assert diagnosis.root_cause_status == "known"
    assert diagnosis.facts[0].category == "NODE_FAILED"
    assert diagnosis.facts[0].retryability == "retryable"
    assert diagnosis.facts[0].subject_id == "sub-02"
    assert diagnosis.goal_gaps[0].reason_code == "ARTIFACT_MISSING"
    assert diagnosis.bindings.observation_hash == observation.observation_hash
    assert calculate_diagnosis_hash(diagnosis) == diagnosis.diagnosis_hash


def test_diagnosis_rejects_cross_bound_evidence():
    observation = _observation()
    changed = _evaluation(observation).model_copy(update={"plan_hash": "other-plan"})
    with pytest.raises(SafetyError, match="RECOVERY_EVIDENCE_BINDING_MISMATCH"):
        RunDiagnosisService(get_node_contract).build(
            observation=observation,
            evaluation=changed,
            ticket=_ticket(),
        )


def test_conflicting_evidence_forces_unknown_root_and_safety_block():
    observation = _observation(conflict=True)
    diagnosis = RunDiagnosisService(get_node_contract).build(
        observation=observation,
        evaluation=_evaluation(observation),
        ticket=_ticket(),
        created_at=NOW,
    )
    assert diagnosis.root_cause_status == "unknown"
    assert "PIPELINE_NODE_STATE_COUNT_CONFLICT" in diagnosis.blocking_safety_issues
    assert any(fact.category == "EVIDENCE_CONFLICT" for fact in diagnosis.facts)


def test_legacy_advisory_is_read_only_unknown_and_never_authority(tmp_path):
    observation = _observation()
    evaluation = _evaluation(observation)
    bindings = RecoveryBindings(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        reviewed_plan_id="reviewed-1",
        plan_hash="plan-hash",
        execution_ticket_id="ticket-1",
        run_id="run-1",
        goal_contract_id="goal-1",
        goal_contract_hash="goal-hash",
        observation_id=observation.observation_id,
        observation_hash=observation.observation_hash,
        goal_evaluation_id=evaluation.goal_evaluation_id,
        goal_evaluation_hash=evaluation.goal_evaluation_hash,
    )
    legacy = {
        "diagnosis_path": "work/diagnosis/run-1/diagnosis.json",
        "issues": [
            {
                "category": "OUT_OF_MEMORY",
                "node": "functional_connectivity_subject",
                "subject_id": "sub-02",
                "message": "legacy classifier suggestion",
                "retry_recommendation": "SAFE_RETRY",
            }
        ],
    }
    record = adapt_legacy_diagnosis(legacy=legacy, bindings=bindings, created_at=NOW)
    assert record.root_cause_status == "unknown"
    assert record.facts[0].retryability == "unknown"
    assert record.blocking_safety_issues == ("LEGACY_ADVISORY_NOT_EXECUTION_AUTHORITY",)
    store = SQLiteDesktopStore(tmp_path / "state.sqlite")
    store.add_recovery_diagnosis(record)
    assert SQLiteDesktopStore(store.db_path).get_recovery_diagnosis(record.diagnosis_id) == record
    with pytest.raises(sqlite3.IntegrityError):
        store.add_recovery_diagnosis(record)


def test_recovery_command_persists_references_without_executing(tmp_path):
    store = SQLiteDesktopStore(tmp_path / "command-state.sqlite")
    quota = {
        "max_lifecycle_recovery_attempts": 3,
        "max_node_attempts": 2,
        "max_subject_node_attempts": 2,
        "max_replans": 2,
        "max_recovery_wall_seconds": 1200,
    }
    store.add_project(
        ProjectDetail(
            id="project-1",
            name="Recovery command",
            study_id="project-1",
            modality="rs-fMRI",
            created_date="test",
            subjects_count=2,
            current_pipeline_id="fc-plan",
            sequences=[],
            scans_count=0,
            total_size="0 B",
            current_model_id="none",
            metadata={"recovery_policy": quota},
        ),
        health_status="Ready",
        rawdata_dir="",
    )
    plan = {
        "pipeline_id": "fc-plan",
        "nodes": [
            {
                "id": "functional_connectivity_subject",
                "backend": "python",
                "depends_on": [],
                "params": {"roi_count": 4},
            }
        ],
        "metadata": {"subject_ids": ["sub-01", "sub-02"]},
    }
    store.add_reviewed_plan(
        ReviewedPlanRecord(
            reviewed_plan_id="reviewed-1",
            project_id="project-1",
            project_config_path=str(tmp_path / "project.yaml"),
            plan_hash="plan-hash",
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
            payload={"plan": plan},
        )
    )
    ticket = _ticket()
    store.add_execution_ticket(ticket)
    observation = _observation()
    evaluation = _evaluation(observation)
    store.add_observation(observation)
    store.add_goal_evaluation(evaluation)
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="lifecycle-1",
        project_id="project-1",
        state="DIAGNOSING",
        reviewed_plan_id="reviewed-1",
        execution_ticket_id="ticket-1",
        run_id="run-1",
        observation_id="observation-1",
        goal_contract_id="goal-1",
        goal_contract_hash="goal-hash",
        goal_evaluation_id="evaluation-1",
        created_at=NOW,
        updated_at=NOW,
        last_command_id="fixture",
    )
    store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id="event-fixture",
            lifecycle_id="lifecycle-1",
            project_id="project-1",
            command_id="fixture",
            actor="test",
            source_command="fixture",
            occurred_at=NOW,
            from_state=None,
            to_state="DIAGNOSING",
            reviewed_plan_id="reviewed-1",
            execution_ticket_id="ticket-1",
            run_id="run-1",
            observation_id="observation-1",
            goal_contract_id="goal-1",
            goal_evaluation_id="evaluation-1",
        ),
    )
    app.dependency_overrides[get_project_store] = lambda: store
    try:
        response = TestClient(app).post(
            "/api/projects/project-1/agent-lifecycles/lifecycle-1/recovery-proposals",
            json={"command_id": "propose-1", "actor": "diagnoser"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["lifecycle"]["state"] == "RECOVERY_PROPOSED"
        assert body["lifecycle"]["diagnosis_id"] == body["diagnosis"]["diagnosis_id"]
        assert (
            body["lifecycle"]["recovery_proposal_id"]
            == body["recovery_proposal"]["recovery_proposal_id"]
        )
        assert store.list_execution_ticket_events(ticket.execution_ticket_id) == []
    finally:
        app.dependency_overrides.pop(get_project_store, None)
