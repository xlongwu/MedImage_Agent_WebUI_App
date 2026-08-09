from __future__ import annotations

from src.backend.app.schemas.recovery import RecoveryChangeRequest
from src.backend.app.services.replan_service import ReplanService
from tests.helpers_phase8 import build_recovery_fixture


def test_replan_persists_complete_new_plan_with_new_identity_and_pending_approval(tmp_path):
    fixture = build_recovery_fixture(
        tmp_path,
        changes=RecoveryChangeRequest(subject_scope=("sub-01",)),
    )
    candidate = next(item for item in fixture.proposal.candidates if item.action == "REPLAN")
    assert candidate.eligible and candidate.changes_reviewed_plan and not candidate.executable

    lifecycle, reviewed, attempt = ReplanService(fixture.store).create_replan(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=candidate.candidate_id,
        command_id="create-replan-1",
        actor="local-reviewer",
    )
    assert reviewed.reviewed_plan_id != fixture.reviewed.reviewed_plan_id
    assert reviewed.plan_hash != fixture.reviewed.plan_hash
    assert reviewed.revision_no == fixture.reviewed.revision_no + 1
    assert reviewed.parent_reviewed_plan_id == fixture.reviewed.reviewed_plan_id
    assert reviewed.parent_plan_hash == fixture.reviewed.plan_hash
    assert reviewed.revision_reason == "recovery_replan"
    assert reviewed.planning_inputs_hash
    assert reviewed.evidence_snapshot_hash
    assert reviewed.status == "NEEDS_APPROVAL"
    assert reviewed.approval_status == "PENDING"
    assert reviewed.payload["approval_envelope"]["summary_hash"]
    assert reviewed.payload["plan"]["metadata"]["subject_ids"] == ["sub-01"]
    assert reviewed.payload["lineage"] == {
        "parent_reviewed_plan_id": fixture.reviewed.reviewed_plan_id,
        "parent_plan_hash": fixture.reviewed.plan_hash,
        "recovery_proposal_id": fixture.proposal.recovery_proposal_id,
        "recovery_candidate_id": candidate.candidate_id,
        "recovery_action": "REPLAN",
        "quota_reservation_id": attempt.quota_reservation_id,
    }
    assert attempt.status == "REPLAN_CREATED"
    assert lifecycle.state == "WAITING_FOR_APPROVAL"
    assert lifecycle.reviewed_plan_id == reviewed.reviewed_plan_id
    assert lifecycle.execution_ticket_id is None
    assert lifecycle.parent_execution_ticket_id == fixture.parent.execution_ticket_id
    assert lifecycle.parent_run_id == "parent-run"
    reservations = fixture.store.list_recovery_quota_reservations(
        fixture.project_id, lifecycle_id=fixture.lifecycle_id
    )
    assert len(reservations) == 1 and reservations[0].status == "consumed"

    replay_lifecycle, replay_reviewed, replay_attempt = ReplanService(fixture.store).create_replan(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        proposal_id=fixture.proposal.recovery_proposal_id,
        candidate_id=candidate.candidate_id,
        command_id="create-replan-1",
        actor="local-reviewer",
    )
    assert replay_lifecycle == lifecycle
    assert replay_reviewed.reviewed_plan_id == reviewed.reviewed_plan_id
    assert replay_reviewed.revision_no == reviewed.revision_no
    assert replay_attempt.recovery_attempt_id == attempt.recovery_attempt_id
    assert len(fixture.store.list_recovery_quota_reservations(fixture.project_id)) == 1
