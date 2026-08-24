"""Command/query HTTP adapter for the persisted Agent lifecycle."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_lifecycle import (
    AgentLifecycleState,
    LifecycleCommand,
    LifecycleCreateRequest,
)
from src.backend.app.schemas.recovery import CheckpointEvidence, RecoveryChangeRequest
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.recovery_execution_service import RecoveryExecutionService
from src.backend.app.services.recovery_policy_service import RecoveryPolicyService
from src.backend.app.services.replan_service import ReplanService

router = APIRouter(prefix="/api/projects/{project_id}/agent-lifecycles")


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    actor: str
    previous_observation_id: str | None = None
    recovery_attempt_id: str | None = None


class RetryProposalRequest(BaseModel):
    command_id: str
    actor: str
    node_ids: list[str]
    backend_ids: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    input_roots: list[str]
    output_roots: list[str]
    classifier: str
    risk: str = "unknown"


class GoalEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    actor: str
    previous_goal_evaluation_id: str | None = None


class RecoveryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    actor: str
    changes: RecoveryChangeRequest | None = None
    checkpoint: CheckpointEvidence | None = None
    parent_recovery_proposal_id: str | None = None


class RecoveryApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    actor: str
    candidate_id: str
    expires_in_seconds: int = Field(default=900, ge=1, le=3600)


class RecoveryExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    actor: str
    candidate_id: str


class RecoveryReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    actor: str
    candidate_id: str


class RecoveryApprovalRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    actor: str
    reason_code: str = "RECOVERY_APPROVAL_REVOKED"


_COMMAND_TARGETS: dict[str, AgentLifecycleState] = {
    "context_ready": "CONTEXT_READY",
    "plan_drafted": "PLAN_DRAFTED",
    "plan_validated": "PLAN_VALIDATED",
    "request_approval": "WAITING_FOR_APPROVAL",
    "approve": "APPROVED",
    "execution_ready": "EXECUTION_READY",
    "diagnose": "DIAGNOSING",
    "request_retry_approval": "WAITING_FOR_RETRY_APPROVAL",
    "human_handoff": "HUMAN_HANDOFF",
    "plan_changed": "PLAN_DRAFTED",
}


@router.post("")
def create_lifecycle(
    project_id: str,
    request: LifecycleCreateRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    record = AgentOrchestrator(store).create(
        project_id=project_id,
        command_id=request.command_id,
        actor=request.actor,
    )
    return {"lifecycle": record}


@router.get("")
def list_lifecycles(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    if store.get_project(project_id) is None:
        raise SafetyError("LIFECYCLE_PROJECT_NOT_FOUND", code="LIFECYCLE_PROJECT_NOT_FOUND", status_code=404)
    return {"project_id": project_id, "lifecycles": store.list_agent_lifecycles(project_id)}


@router.get("/{lifecycle_id}")
def get_lifecycle(
    project_id: str,
    lifecycle_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    orchestrator = AgentOrchestrator(store)
    return {
        "lifecycle": orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id),
        "events": orchestrator.events(project_id=project_id, lifecycle_id=lifecycle_id),
    }


@router.post("/{lifecycle_id}/commands")
def apply_lifecycle_command(
    project_id: str,
    lifecycle_id: str,
    command: LifecycleCommand,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    target = _COMMAND_TARGETS.get(command.action)
    if target is None:
        raise SafetyError("LIFECYCLE_COMMAND_UNSUPPORTED", code="LIFECYCLE_COMMAND_UNSUPPORTED")
    updates = {
        key: value
        for key, value in {
            "reviewed_plan_id": command.reviewed_plan_id,
            "goal_contract_id": command.goal_contract_id,
            "goal_contract_hash": command.goal_contract_hash,
            "execution_ticket_id": command.execution_ticket_id,
            "audit_id": command.audit_id,
            "run_id": command.run_id,
        }.items()
        if value is not None
    }
    record = AgentOrchestrator(store).transition(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        to_state=target,
        command_id=command.command_id,
        actor=command.actor,
        source_command=command.action,
        reason=command.reason,
        updates=updates,
        details=command.details,
    )
    return {"lifecycle": record}


@router.post("/{lifecycle_id}/observations")
def observe_lifecycle(
    project_id: str,
    lifecycle_id: str,
    request: ObservationRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    record = AgentOrchestrator(store).observe(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        command_id=request.command_id,
        actor=request.actor,
        previous_observation_id=request.previous_observation_id,
        recovery_attempt_id=request.recovery_attempt_id,
    )
    observation = store.get_observation(record.observation_id or "")
    return {"lifecycle": record, "observation": observation}


@router.get("/{lifecycle_id}/observations")
def list_lifecycle_observations(
    project_id: str,
    lifecycle_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    return {
        "project_id": project_id,
        "lifecycle_id": lifecycle_id,
        "observations": store.list_observations(project_id, lifecycle_id=lifecycle_id),
    }


@router.get("/{lifecycle_id}/observations/{observation_id}")
def get_lifecycle_observation(
    project_id: str,
    lifecycle_id: str,
    observation_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    observation = store.get_observation(observation_id)
    if (
        observation is None
        or observation.bindings.project_id != project_id
        or observation.bindings.lifecycle_id != lifecycle_id
    ):
        raise SafetyError("OBSERVATION_NOT_FOUND", code="OBSERVATION_NOT_FOUND", status_code=404)
    return {"observation": observation}


@router.post("/{lifecycle_id}/goal-evaluations")
def evaluate_lifecycle_goal(
    project_id: str,
    lifecycle_id: str,
    request: GoalEvaluationRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    lifecycle, evaluation = AgentOrchestrator(store).evaluate_goal(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        command_id=request.command_id,
        actor=request.actor,
        previous_goal_evaluation_id=request.previous_goal_evaluation_id,
    )
    return {"lifecycle": lifecycle, "goal_evaluation": evaluation}


@router.get("/{lifecycle_id}/goal-evaluations")
def list_lifecycle_goal_evaluations(
    project_id: str,
    lifecycle_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    return {
        "project_id": project_id,
        "lifecycle_id": lifecycle_id,
        "goal_evaluations": store.list_goal_evaluations(
            project_id,
            lifecycle_id=lifecycle_id,
        ),
    }


@router.get("/{lifecycle_id}/goal-evaluations/{goal_evaluation_id}")
def get_lifecycle_goal_evaluation(
    project_id: str,
    lifecycle_id: str,
    goal_evaluation_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    evaluation = store.get_goal_evaluation(goal_evaluation_id)
    if (
        evaluation is None
        or evaluation.project_id != project_id
        or evaluation.lifecycle_id != lifecycle_id
    ):
        raise SafetyError("GOAL_EVALUATION_NOT_FOUND", code="GOAL_EVALUATION_NOT_FOUND", status_code=404)
    return {"goal_evaluation": evaluation}


@router.post("/{lifecycle_id}/recovery-proposals")
def create_recovery_proposal(
    project_id: str,
    lifecycle_id: str,
    request: RecoveryProposalRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    lifecycle, diagnosis, proposal = AgentOrchestrator(store).propose_recovery(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        command_id=request.command_id,
        actor=request.actor,
        changes=request.changes,
        checkpoint=request.checkpoint,
        parent_recovery_proposal_id=request.parent_recovery_proposal_id,
    )
    return {
        "lifecycle": lifecycle,
        "diagnosis": diagnosis,
        "recovery_proposal": proposal,
    }


@router.get("/{lifecycle_id}/recovery-diagnoses")
def list_recovery_diagnoses(
    project_id: str,
    lifecycle_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    return {
        "project_id": project_id,
        "lifecycle_id": lifecycle_id,
        "diagnoses": store.list_recovery_diagnoses(project_id, lifecycle_id=lifecycle_id),
    }


@router.get("/{lifecycle_id}/recovery-diagnoses/{diagnosis_id}")
def get_recovery_diagnosis(
    project_id: str,
    lifecycle_id: str,
    diagnosis_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    diagnosis = store.get_recovery_diagnosis(diagnosis_id)
    if (
        diagnosis is None
        or diagnosis.bindings.project_id != project_id
        or diagnosis.bindings.lifecycle_id != lifecycle_id
    ):
        raise SafetyError("RECOVERY_DIAGNOSIS_NOT_FOUND", code="RECOVERY_DIAGNOSIS_NOT_FOUND", status_code=404)
    return {"diagnosis": diagnosis}


@router.get("/{lifecycle_id}/recovery-proposals")
def list_recovery_proposals(
    project_id: str,
    lifecycle_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    return {
        "project_id": project_id,
        "lifecycle_id": lifecycle_id,
        "recovery_proposals": store.list_recovery_proposals(
            project_id,
            lifecycle_id=lifecycle_id,
        ),
    }


@router.get("/{lifecycle_id}/recovery-proposals/{proposal_id}")
def get_recovery_proposal(
    project_id: str,
    lifecycle_id: str,
    proposal_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    proposal = store.get_recovery_proposal(proposal_id)
    if (
        proposal is None
        or proposal.bindings.project_id != project_id
        or proposal.bindings.lifecycle_id != lifecycle_id
    ):
        raise SafetyError("RECOVERY_PROPOSAL_NOT_FOUND", code="RECOVERY_PROPOSAL_NOT_FOUND", status_code=404)
    return {"recovery_proposal": proposal}


@router.post("/{lifecycle_id}/recovery-proposals/{proposal_id}/approve")
def approve_recovery_proposal(
    project_id: str,
    lifecycle_id: str,
    proposal_id: str,
    request: RecoveryApprovalRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    lifecycle, approval = RecoveryExecutionService(store).approve(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        proposal_id=proposal_id,
        candidate_id=request.candidate_id,
        command_id=request.command_id,
        actor=request.actor,
        expires_in_seconds=request.expires_in_seconds,
    )
    return {"lifecycle": lifecycle, "recovery_approval": approval}


@router.post("/{lifecycle_id}/recovery-proposals/{proposal_id}/execute")
def execute_recovery_proposal(
    project_id: str,
    lifecycle_id: str,
    proposal_id: str,
    request: RecoveryExecutionRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    lifecycle, attempt, result = RecoveryExecutionService(store).execute(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        proposal_id=proposal_id,
        candidate_id=request.candidate_id,
        command_id=request.command_id,
        actor=request.actor,
    )
    return {"lifecycle": lifecycle, "recovery_attempt": attempt, "result": result}


@router.post("/{lifecycle_id}/recovery-proposals/{proposal_id}/create-replan")
def create_recovery_replan(
    project_id: str,
    lifecycle_id: str,
    proposal_id: str,
    request: RecoveryReplanRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    lifecycle, reviewed_plan, attempt = ReplanService(store).create_replan(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        proposal_id=proposal_id,
        candidate_id=request.candidate_id,
        command_id=request.command_id,
        actor=request.actor,
    )
    return {
        "lifecycle": lifecycle,
        "reviewed_plan": reviewed_plan,
        "recovery_attempt": attempt,
    }


@router.post("/{lifecycle_id}/recovery-approvals/{approval_id}/revoke")
def revoke_recovery_approval(
    project_id: str,
    lifecycle_id: str,
    approval_id: str,
    request: RecoveryApprovalRevokeRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    approval = store.get_recovery_approval(approval_id)
    if approval is None or approval.project_id != project_id or approval.lifecycle_id != lifecycle_id:
        raise SafetyError("RECOVERY_APPROVAL_NOT_FOUND", code="RECOVERY_APPROVAL_NOT_FOUND", status_code=404)
    revoked = RecoveryPolicyService(store).revoke(
        approval_id,
        command_id=request.command_id,
        actor=request.actor,
        reason_code=request.reason_code,
    )
    return {"recovery_approval": revoked}


@router.get("/{lifecycle_id}/recovery-attempts")
def list_recovery_attempts(
    project_id: str,
    lifecycle_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    return {
        "project_id": project_id,
        "lifecycle_id": lifecycle_id,
        "recovery_attempts": store.list_recovery_attempts(project_id, lifecycle_id=lifecycle_id),
    }


@router.get("/{lifecycle_id}/recovery-attempts/{attempt_id}")
def get_recovery_attempt(
    project_id: str,
    lifecycle_id: str,
    attempt_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    AgentOrchestrator(store).get(project_id=project_id, lifecycle_id=lifecycle_id)
    attempt = store.get_recovery_attempt(attempt_id)
    if attempt is None or attempt.project_id != project_id or attempt.lifecycle_id != lifecycle_id:
        raise SafetyError("RECOVERY_ATTEMPT_NOT_FOUND", code="RECOVERY_ATTEMPT_NOT_FOUND", status_code=404)
    return {
        "recovery_attempt": attempt,
        "events": store.list_recovery_attempt_events(attempt_id),
    }


@router.post("/{lifecycle_id}/retry-proposals")
def propose_retry(
    project_id: str,
    lifecycle_id: str,
    request: RetryProposalRequest,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, object]:
    record = AgentOrchestrator(store).propose_retry(
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        command_id=request.command_id,
        actor=request.actor,
        node_ids=request.node_ids,
        backend_ids=request.backend_ids,
        params=request.params,
        input_roots=request.input_roots,
        output_roots=request.output_roots,
        classifier=request.classifier,
        risk=request.risk,
    )
    return {"lifecycle": record}
