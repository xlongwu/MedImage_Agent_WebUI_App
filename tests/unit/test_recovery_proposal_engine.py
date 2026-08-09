from __future__ import annotations

import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.main import app
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.execution_gateway import ExecutionGateway
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.schemas.execution_ticket import ExecutionRetryPolicy, ExecutionTicket
from src.backend.app.schemas.recovery import (
    CheckpointEvidence,
    DiagnosisFact,
    DiagnosisRecord,
    GoalGap,
    RecoveryBindings,
    RecoveryChangeRequest,
    RecoveryQuotaLimits,
    RecoveryQuotaUsage,
)
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.recovery_proposal_engine import (
    RecoveryProposalEngine,
    calculate_recovery_proposal_hash,
)

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
NODE = "functional_connectivity_subject"


def _plan():
    return {
        "pipeline_id": "fc-plan",
        "nodes": [
            {
                "id": NODE,
                "backend": "python",
                "depends_on": [],
                "params": {"roi_count": 4},
            }
        ],
        "metadata": {"subject_ids": ["sub-01", "sub-02"]},
    }


def _ticket(**policy_overrides) -> ExecutionTicket:
    policy = {
        "max_retry_count": 2,
        "allowed_node_ids": (NODE,),
        "require_approval": True,
        "max_lifecycle_recovery_attempts": 2,
        "max_node_attempts": 1,
        "max_subject_node_attempts": 1,
        "max_replans": 1,
        "max_recovery_wall_seconds": 600,
    }
    policy.update(policy_overrides)
    return ExecutionTicket(
        execution_ticket_id="ticket-1",
        project_id="project-1",
        reviewed_plan_id="reviewed-1",
        plan_hash="plan-hash",
        goal_contract_hash="goal-hash",
        evaluation_policy_version="goal-evaluator-v1",
        approval_summary_hash="approval-1",
        approved_actor="reviewer",
        approved_node_ids=(NODE,),
        approved_backend_ids=("python",),
        input_roots=("project/inputs",),
        output_roots=("project/derivatives",),
        readonly_roots=("project/rawdata",),
        project_config_path="project/project.yaml",
        pipeline_path="project/pipeline.yaml",
        scope_hash=stable_hash(
            {
                "input_roots": ["project/inputs"],
                "output_roots": ["project/derivatives"],
                "readonly_roots": ["project/rawdata"],
            }
        ),
        allowlist_hash="allowlist-1",
        normalized_params_hash=stable_hash({NODE: {"roi_count": 4}}),
        contract_versions=((NODE, "1.0.0"),),
        audit_id="audit-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        retry_policy=ExecutionRetryPolicy(**policy),
        canonical_hash="ticket-hash",
    )


def _bindings() -> RecoveryBindings:
    return RecoveryBindings(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        reviewed_plan_id="reviewed-1",
        plan_hash="plan-hash",
        execution_ticket_id="ticket-1",
        run_id="run-1",
        goal_contract_id="goal-1",
        goal_contract_hash="goal-hash",
        observation_id="observation-1",
        observation_hash="observation-hash",
        goal_evaluation_id="evaluation-1",
        goal_evaluation_hash="evaluation-hash",
    )


def _diagnosis(
    *,
    category: str = "NODE_FAILED",
    retryability: str = "retryable",
    subject_id: str | None = "sub-02",
    root: str = "known",
    blocking=(),
) -> DiagnosisRecord:
    fact = DiagnosisFact(
        fact_id="fact-1",
        category=category,
        scope="subject" if subject_id else "node",
        node_id=NODE,
        subject_id=subject_id,
        evidence_ids=("node-source",),
        confidence_source="contract_rule",
        retryability=retryability,
        message=category,
    )
    gap = GoalGap(
        criterion_id="fc-present",
        criterion_type="artifact_present",
        status="failed",
        reason_code="ARTIFACT_MISSING",
        expected={"artifact_type": "fc_matrix"},
        actual={"failed": 1},
        affected_subjects=("sub-02",),
    )
    payload = {
        "bindings": _bindings().model_dump(mode="json"),
        "facts": [fact.model_dump(mode="json")],
        "gaps": [gap.model_dump(mode="json")],
        "root": root,
        "blocking": list(blocking),
    }
    record = DiagnosisRecord(
        diagnosis_id=f"diagnosis_{stable_hash(payload)[:20]}",
        bindings=_bindings(),
        created_at=NOW,
        facts=(fact,),
        goal_gaps=(gap,),
        root_cause_status=root,
        blocking_safety_issues=blocking,
        diagnosis_hash="pending",
    )
    raw = record.model_dump(mode="json")
    raw.pop("diagnosis_hash")
    return record.model_copy(update={"diagnosis_hash": stable_hash(raw)})


def _project_quota(**overrides):
    values = {
        "max_lifecycle_recovery_attempts": 3,
        "max_node_attempts": 2,
        "max_subject_node_attempts": 2,
        "max_replans": 2,
        "max_recovery_wall_seconds": 1200,
    }
    values.update(overrides)
    return RecoveryQuotaLimits(**values)


def _resolver_with(contract):
    def resolve(node_id):
        if node_id == contract.node_id:
            return contract
        return get_node_contract(node_id)

    return resolve


def _propose(**overrides):
    values = {
        "diagnosis": _diagnosis(),
        "plan": _plan(),
        "ticket": _ticket(),
        "project_policy": _project_quota(),
        "usage": RecoveryQuotaUsage(),
        "created_at": NOW,
    }
    values.update(overrides)
    return RecoveryProposalEngine(get_node_contract).propose(**values)


def test_safe_retry_and_exact_failed_subject_retry_are_deterministic():
    first = _propose()
    second = _propose()
    actions = {candidate.action for candidate in first.candidates}
    assert {"SAFE_RETRY", "RETRY_FAILED_SUBJECTS"}.issubset(actions)
    recommended = next(
        candidate
        for candidate in first.candidates
        if candidate.candidate_id == first.recommended_candidate_id
    )
    assert recommended.action == "RETRY_FAILED_SUBJECTS"
    assert recommended.target_subject_ids == ("sub-02",)
    assert recommended.executable is True
    assert recommended.approval_class == "explicit_retry_approval"
    assert first.recovery_proposal_id == second.recovery_proposal_id
    assert first.recovery_proposal_hash == second.recovery_proposal_hash
    assert calculate_recovery_proposal_hash(first) == first.recovery_proposal_hash


def test_failed_subject_retry_is_blocked_when_contract_does_not_allow_subset():
    base = get_node_contract(NODE)
    policy = base.retry_policy.model_copy(update={"supports_subject_subset": False})
    contract = base.model_copy(update={"retry_policy": policy})
    proposal = RecoveryProposalEngine(_resolver_with(contract)).propose(
        diagnosis=_diagnosis(),
        plan=_plan(),
        ticket=_ticket(),
        project_policy=_project_quota(),
        usage=RecoveryQuotaUsage(),
        created_at=NOW,
    )
    candidate = next(item for item in proposal.candidates if item.action == "RETRY_FAILED_SUBJECTS")
    assert candidate.eligible is False
    assert "SUBJECT_SUBSET_CONTRACT_NOT_SUPPORTED" in candidate.blocked_reasons


def test_verified_contract_checkpoint_can_produce_resume():
    base = get_node_contract(NODE)
    policy = base.retry_policy.model_copy(
        update={"supports_resume": True, "checkpoint_schema": "fc-checkpoint-v1"}
    )
    contract = base.model_copy(update={"retry_policy": policy})
    checkpoint = CheckpointEvidence(
        checkpoint_id="checkpoint-1",
        schema_id="fc-checkpoint-v1",
        verified=True,
        plan_hash="plan-hash",
        normalized_params_hash=_ticket().normalized_params_hash,
        backend_ids=("python",),
        input_roots=("project/inputs",),
        output_roots=("project/derivatives",),
        completed_node_ids=(),
        remaining_node_ids=(NODE,),
        evidence_ids=("checkpoint-validator",),
    )
    proposal = RecoveryProposalEngine(_resolver_with(contract)).propose(
        diagnosis=_diagnosis(),
        plan=_plan(),
        ticket=_ticket(),
        project_policy=_project_quota(),
        usage=RecoveryQuotaUsage(),
        checkpoint=checkpoint,
        created_at=NOW,
    )
    resume = next(item for item in proposal.candidates if item.action == "RESUME")
    assert resume.eligible and resume.executable
    assert resume.checkpoint_id == "checkpoint-1"


def test_parameter_backend_and_replan_actions_require_new_review():
    parameter = _propose(
        diagnosis=_diagnosis(category="PARAMETER_CAUSED_GAP", retryability="non_retryable"),
        changes=RecoveryChangeRequest(parameter_patch={NODE: {"roi_count": 8}}),
    )
    parameter_candidate = next(
        item for item in parameter.candidates if item.action == "PARAMETER_CHANGE"
    )
    assert parameter_candidate.eligible and not parameter_candidate.executable
    assert parameter_candidate.changes_reviewed_plan
    assert parameter_candidate.approval_class == "new_reviewed_plan_and_approval"

    base = get_node_contract(NODE)
    backend_policy = base.retry_policy.model_copy(
        update={
            "backend_switch_targets": ("gpu",),
            "backend_scientific_equivalence": {"gpu": "Requires reviewed tolerance validation"},
        }
    )
    backend_contract = base.model_copy(update={"retry_policy": backend_policy})
    backend = RecoveryProposalEngine(_resolver_with(backend_contract)).propose(
        diagnosis=_diagnosis(category="BACKEND_UNAVAILABLE", retryability="non_retryable"),
        plan=_plan(),
        ticket=_ticket(),
        project_policy=_project_quota(),
        usage=RecoveryQuotaUsage(),
        changes=RecoveryChangeRequest(backend_patch={NODE: "gpu"}),
        created_at=NOW,
    )
    backend_candidate = next(item for item in backend.candidates if item.action == "BACKEND_SWITCH")
    assert backend_candidate.risk == "high"
    assert backend_candidate.eligible is False
    assert backend.summary().recommended_action == "HUMAN_HANDOFF"

    replan = _propose(
        diagnosis=_diagnosis(category="SCOPE_INCOMPLETE", retryability="non_retryable"),
        changes=RecoveryChangeRequest(subject_scope=("sub-01",)),
    )
    replan_candidate = next(item for item in replan.candidates if item.action == "REPLAN")
    assert replan_candidate.eligible and not replan_candidate.executable
    assert replan_candidate.changes_reviewed_plan


@pytest.mark.parametrize(
    "changes",
    [
        RecoveryChangeRequest(parameter_patch={NODE: {"roi_count": 8}}),
        RecoveryChangeRequest(backend_patch={NODE: "gpu"}),
        RecoveryChangeRequest(replacement_node_ids=("contract_smoke",)),
        RecoveryChangeRequest(
            dag_patch={NODE: ("contract_smoke",)}, replacement_node_ids=("contract_smoke", NODE)
        ),
        RecoveryChangeRequest(output_roots=("project/other-derivatives",)),
        RecoveryChangeRequest(subject_scope=("sub-01",)),
        RecoveryChangeRequest(goal_contract_hash="changed-goal"),
        RecoveryChangeRequest(approval_summary_hash="changed-approval"),
        RecoveryChangeRequest(allowlist_hash="changed-allowlist"),
    ],
)
def test_every_reviewed_contract_dimension_change_is_canonical_and_never_safe(changes):
    diagnosis = _diagnosis(category="SCOPE_INCOMPLETE", retryability="non_retryable")
    proposal = _propose(diagnosis=diagnosis, changes=changes)
    for candidate in proposal.candidates:
        if candidate.action in {"SAFE_RETRY", "RETRY_FAILED_SUBJECTS", "RESUME"}:
            assert not candidate.eligible
            assert "REVIEWED_CONTRACT_CHANGED" in candidate.blocked_reasons
    changed_candidates = [
        candidate
        for candidate in proposal.candidates
        if candidate.canonical_diff.changes_reviewed_contract
    ]
    assert changed_candidates
    assert all(
        any(entry.changed for entry in candidate.canonical_diff.entries)
        for candidate in changed_candidates
    )


@pytest.mark.parametrize(
    ("diagnosis", "ticket", "policy", "usage", "reason"),
    [
        (
            _diagnosis(root="unknown"),
            _ticket(),
            _project_quota(),
            RecoveryQuotaUsage(),
            "ROOT_CAUSE_UNKNOWN",
        ),
        (
            _diagnosis(blocking=("EVIDENCE_CONFLICT",)),
            _ticket(),
            _project_quota(),
            RecoveryQuotaUsage(),
            "EVIDENCE_CONFLICT",
        ),
        (
            _diagnosis(),
            _ticket(),
            RecoveryQuotaLimits(),
            RecoveryQuotaUsage(),
            "RECOVERY_QUOTA_DIMENSION_MISSING",
        ),
        (
            _diagnosis(),
            _ticket(),
            _project_quota(),
            RecoveryQuotaUsage(node_attempts=1),
            "RECOVERY_QUOTA_EXHAUSTED",
        ),
    ],
)
def test_unknown_conflict_missing_or_exhausted_quota_produces_only_handoff(
    diagnosis, ticket, policy, usage, reason
):
    proposal = _propose(
        diagnosis=diagnosis,
        ticket=ticket,
        project_policy=policy,
        usage=usage,
    )
    assert len(proposal.candidates) == 1
    handoff = proposal.candidates[0]
    assert handoff.action == "HUMAN_HANDOFF"
    assert handoff.executable is False
    assert reason in handoff.reason_codes


def test_unknown_contract_and_rawdata_output_change_force_handoff():
    unknown_ticket = _ticket().model_copy(
        update={
            "approved_node_ids": ("unknown_recovery_node",),
            "contract_versions": (("unknown_recovery_node", "1"),),
        }
    )
    unknown = _propose(ticket=unknown_ticket)
    assert unknown.summary().recommended_action == "HUMAN_HANDOFF"
    assert any("NODE_CONTRACT_UNKNOWN" in reason for reason in unknown.candidates[0].reason_codes)

    rawdata = _propose(
        diagnosis=_diagnosis(category="SCOPE_INCOMPLETE", retryability="non_retryable"),
        changes=RecoveryChangeRequest(output_roots=("project/rawdata",)),
    )
    assert rawdata.summary().recommended_action == "HUMAN_HANDOFF"
    assert any(
        "RAWDATA_OUTPUT_SCOPE_FORBIDDEN" in candidate.blocked_reasons
        for candidate in rawdata.candidates
        if candidate.action == "REPLAN"
    )


def test_quota_uses_strictest_ticket_contract_and_project_limit():
    proposal = _propose(
        project_policy=_project_quota(max_lifecycle_recovery_attempts=1),
        usage=RecoveryQuotaUsage(lifecycle_recovery_attempts=1),
    )
    assert proposal.quota.effective_limits["max_lifecycle_recovery_attempts"] == 1
    assert proposal.quota.exhausted_dimensions == ("max_lifecycle_recovery_attempts",)
    assert proposal.summary().recommended_action == "HUMAN_HANDOFF"


def test_proposal_engine_has_no_execution_or_filesystem_side_effects(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("side effect invoked")

    monkeypatch.setattr(ExecutionGateway, "dispatch", forbidden)
    monkeypatch.setattr(ExecutionTicketService, "issue", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    before = tuple(tmp_path.rglob("*"))
    proposal = _propose()
    after = tuple(tmp_path.rglob("*"))
    assert proposal.candidates
    assert before == after == ()


def test_proposal_persistence_is_immutable_and_reloadable(tmp_path):
    proposal = _propose()
    store = SQLiteDesktopStore(tmp_path / "state.sqlite")
    store.add_recovery_proposal(proposal)
    reopened = SQLiteDesktopStore(store.db_path)
    assert reopened.get_recovery_proposal(proposal.recovery_proposal_id) == proposal
    assert reopened.list_recovery_proposals("project-1", lifecycle_id="lifecycle-1") == [proposal]
    with pytest.raises(sqlite3.IntegrityError):
        store.add_recovery_proposal(proposal)


def test_lifecycle_references_and_read_only_recovery_api_are_project_scoped(tmp_path):
    diagnosis = _diagnosis()
    proposal = _propose(diagnosis=diagnosis)
    store = SQLiteDesktopStore(tmp_path / "api-state.sqlite")
    store.add_project(
        ProjectDetail(
            id="project-1",
            name="Recovery API",
            study_id="project-1",
            modality="rs-fMRI",
            created_date="test",
            subjects_count=2,
            current_pipeline_id="fc-plan",
            sequences=[],
            scans_count=0,
            total_size="0 B",
            current_model_id="none",
        ),
        health_status="Ready",
        rawdata_dir="",
    )
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
    event = AgentLifecycleEvent(
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
    )
    store.create_agent_lifecycle(lifecycle, event)
    store.add_recovery_diagnosis(diagnosis)
    store.add_recovery_proposal(proposal)
    updated = AgentOrchestrator(store).transition(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        to_state="RECOVERY_PROPOSED",
        command_id="proposal-created",
        actor="diagnoser",
        source_command="recovery_proposal_created",
        updates={
            "diagnosis_id": diagnosis.diagnosis_id,
            "diagnosis_summary": diagnosis.summary(),
            "recovery_proposal_id": proposal.recovery_proposal_id,
            "recovery_proposal_summary": proposal.summary(),
        },
    )
    assert updated.diagnosis_id == diagnosis.diagnosis_id
    assert updated.recovery_proposal_id == proposal.recovery_proposal_id

    app.dependency_overrides[get_project_store] = lambda: store
    try:
        client = TestClient(app)
        diagnoses = client.get(
            "/api/projects/project-1/agent-lifecycles/lifecycle-1/recovery-diagnoses"
        )
        proposals = client.get(
            "/api/projects/project-1/agent-lifecycles/lifecycle-1/recovery-proposals"
        )
        detail = client.get(
            f"/api/projects/project-1/agent-lifecycles/lifecycle-1/recovery-proposals/{proposal.recovery_proposal_id}"
        )
        assert diagnoses.status_code == proposals.status_code == detail.status_code == 200
        assert diagnoses.json()["diagnoses"][0]["diagnosis_id"] == diagnosis.diagnosis_id
        assert (
            proposals.json()["recovery_proposals"][0]["recovery_proposal_id"]
            == proposal.recovery_proposal_id
        )
        assert (
            detail.json()["recovery_proposal"]["recovery_proposal_hash"]
            == proposal.recovery_proposal_hash
        )
        assert (
            client.get(
                f"/api/projects/other/agent-lifecycles/lifecycle-1/recovery-proposals/{proposal.recovery_proposal_id}"
            ).status_code
            == 404
        )
    finally:
        app.dependency_overrides.pop(get_project_store, None)
