"""Persisted, fail-closed coordinator for the Agent lifecycle."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.agent_lifecycle import (
    AgentLifecycleEvent,
    AgentLifecycleRecord,
    AgentLifecycleState,
    RetryProposal,
)
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.goal_contract import GoalEvaluationRecord
from src.backend.app.schemas.observation import ObservationRecord
from src.backend.app.schemas.recovery import (
    CheckpointEvidence,
    DiagnosisRecord,
    RecoveryChangeRequest,
    RecoveryProposal,
    RecoveryQuotaUsage,
)
from src.backend.app.services.goal_evaluator import GoalEvaluator
from src.backend.app.services.observation_collector import ObservationCollector
from src.backend.app.services.recovery_proposal_engine import RecoveryProposalEngine
from src.backend.app.services.run_diagnosis_service import RunDiagnosisService


class AgentLifecycleStore(Protocol):
    def get_project(self, project_id: str) -> object | None: ...
    def create_agent_lifecycle(self, record, event): ...
    def get_agent_lifecycle(self, lifecycle_id: str): ...
    def list_agent_lifecycles(self, project_id: str): ...
    def transition_agent_lifecycle(self, record, event, *, expected_state: str): ...
    def list_agent_lifecycle_events(self, lifecycle_id: str): ...
    def get_execution_ticket(self, execution_ticket_id: str) -> ExecutionTicket | None: ...
    def get_reviewed_plan(self, reviewed_plan_id: str): ...
    def get_run_link_by_run_id(self, project_id: str, run_id: str): ...
    def add_observation(self, record: ObservationRecord) -> ObservationRecord: ...
    def get_observation(self, observation_id: str) -> ObservationRecord | None: ...
    def list_observations(self, project_id: str, *, lifecycle_id: str | None = None, run_id: str | None = None): ...
    def add_goal_evaluation(self, record: GoalEvaluationRecord) -> GoalEvaluationRecord: ...
    def get_goal_evaluation(self, goal_evaluation_id: str) -> GoalEvaluationRecord | None: ...
    def list_goal_evaluations(self, project_id: str, *, lifecycle_id: str | None = None, observation_id: str | None = None): ...
    def add_recovery_diagnosis(self, record: DiagnosisRecord) -> DiagnosisRecord: ...
    def get_recovery_diagnosis(self, diagnosis_id: str) -> DiagnosisRecord | None: ...
    def list_recovery_diagnoses(self, project_id: str, *, lifecycle_id: str | None = None): ...
    def add_recovery_proposal(self, record: RecoveryProposal) -> RecoveryProposal: ...
    def get_recovery_proposal(self, proposal_id: str) -> RecoveryProposal | None: ...
    def list_recovery_proposals(self, project_id: str, *, lifecycle_id: str | None = None): ...


_TRANSITIONS: dict[AgentLifecycleState, frozenset[AgentLifecycleState]] = {
    "CREATED": frozenset({"WAITING_FOR_INPUT", "CONTEXT_READY", "HUMAN_HANDOFF", "CANCELED"}),
    "WAITING_FOR_INPUT": frozenset({"CONTEXT_READY", "HUMAN_HANDOFF", "CANCELED"}),
    "CONTEXT_READY": frozenset(
        {
            "WAITING_FOR_INPUT",
            "WAITING_FOR_SCIENCE_DECISION",
            "PLAN_DRAFTED",
            "HUMAN_HANDOFF",
            "CANCELED",
        }
    ),
    "PLAN_DRAFTED": frozenset({"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION", "PLAN_VALIDATED", "HUMAN_HANDOFF", "CANCELED"}),
    "WAITING_FOR_SCIENCE_DECISION": frozenset({"PLAN_DRAFTED", "HUMAN_HANDOFF", "CANCELED"}),
    "PLAN_VALIDATED": frozenset({"WAITING_FOR_APPROVAL", "SUCCEEDED", "HUMAN_HANDOFF", "CANCELED"}),
    "WAITING_FOR_APPROVAL": frozenset({"APPROVED", "PLAN_DRAFTED", "HUMAN_HANDOFF", "CANCELED"}),
    "APPROVED": frozenset({"EXECUTION_READY", "PLAN_DRAFTED", "HUMAN_HANDOFF", "CANCELED"}),
    "EXECUTION_READY": frozenset({"RUNNING", "FAILED", "PLAN_DRAFTED", "HUMAN_HANDOFF", "CANCELED"}),
    "RUNNING": frozenset({"OBSERVING", "FAILED", "HUMAN_HANDOFF"}),
    "OBSERVING": frozenset({"EVALUATING", "FAILED", "HUMAN_HANDOFF"}),
    "EVALUATING": frozenset({"GOAL_SATISFIED", "DIAGNOSING", "HUMAN_HANDOFF"}),
    "FAILED": frozenset({"DIAGNOSING", "HUMAN_HANDOFF"}),
    "DIAGNOSING": frozenset({"RETRY_PROPOSED", "RECOVERY_PROPOSED", "PLAN_DRAFTED", "HUMAN_HANDOFF"}),
    "RETRY_PROPOSED": frozenset({"WAITING_FOR_RETRY_APPROVAL", "PLAN_DRAFTED", "HUMAN_HANDOFF"}),
    "WAITING_FOR_RETRY_APPROVAL": frozenset({"RETRYING", "PLAN_DRAFTED", "HUMAN_HANDOFF"}),
    "RETRYING": frozenset({"OBSERVING", "FAILED", "HUMAN_HANDOFF"}),
    "RECOVERY_PROPOSED": frozenset({"WAITING_FOR_RECOVERY_APPROVAL", "PLAN_DRAFTED", "HUMAN_HANDOFF"}),
    "WAITING_FOR_RECOVERY_APPROVAL": frozenset({"RECOVERY_READY", "HUMAN_HANDOFF"}),
    "RECOVERY_READY": frozenset({"RECOVERING", "HUMAN_HANDOFF"}),
    "RECOVERING": frozenset({"OBSERVING", "FAILED", "HUMAN_HANDOFF"}),
    "GOAL_SATISFIED": frozenset(),
    "SUCCEEDED": frozenset(),
    "HUMAN_HANDOFF": frozenset(),
    "CANCELED": frozenset(),
}


class AgentOrchestrator:
    def __init__(self, store: AgentLifecycleStore) -> None:
        self.store = store

    def create(
        self,
        *,
        project_id: str,
        command_id: str,
        actor: str,
        goal_text: str | None = None,
        goal_hash: str | None = None,
    ) -> AgentLifecycleRecord:
        if self.store.get_project(project_id) is None:
            raise SafetyError("LIFECYCLE_PROJECT_NOT_FOUND", code="LIFECYCLE_PROJECT_NOT_FOUND")
        now = datetime.now(UTC)
        record = AgentLifecycleRecord(
            lifecycle_id=f"lifecycle_{uuid4().hex}",
            project_id=project_id,
            goal_text=goal_text,
            goal_hash=goal_hash,
            created_actor=actor,
            created_at=now,
            updated_at=now,
            last_command_id=command_id,
        )
        event = self._event(
            record=record,
            command_id=command_id,
            actor=actor,
            source_command="create",
            from_state=None,
            to_state="CREATED",
        )
        try:
            return self.store.create_agent_lifecycle(record, event)
        except sqlite3.IntegrityError as exc:
            raise SafetyError("LIFECYCLE_COMMAND_REPLAYED", code="LIFECYCLE_COMMAND_REPLAYED") from exc

    def cancel(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        command_id: str,
        actor: str,
        reason: str | None = None,
    ) -> AgentLifecycleRecord:
        current = self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if current.state == "CANCELED":
            return current
        if "CANCELED" not in _TRANSITIONS[current.state]:
            raise SafetyError(
                "LIFECYCLE_CANCEL_NOT_SUPPORTED",
                code="LIFECYCLE_CANCEL_NOT_SUPPORTED",
            )
        now = datetime.now(UTC)
        return self.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="CANCELED",
            command_id=command_id,
            actor=actor,
            source_command="cancel",
            reason=reason,
            updates={
                "pending_decision_batch": None,
                "canceled_at": now,
                "canceled_by": actor,
                "cancellation_reason": reason,
            },
        )

    def get(self, *, project_id: str, lifecycle_id: str) -> AgentLifecycleRecord:
        record = self.store.get_agent_lifecycle(lifecycle_id)
        if record is None or record.project_id != project_id:
            raise SafetyError("LIFECYCLE_NOT_FOUND", code="LIFECYCLE_NOT_FOUND", status_code=404)
        return record

    def events(self, *, project_id: str, lifecycle_id: str) -> list[AgentLifecycleEvent]:
        self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        return self.store.list_agent_lifecycle_events(lifecycle_id)

    @staticmethod
    def _event(
        *,
        record: AgentLifecycleRecord,
        command_id: str,
        actor: str,
        source_command: str,
        from_state: AgentLifecycleState | None,
        to_state: AgentLifecycleState,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AgentLifecycleEvent:
        return AgentLifecycleEvent(
            event_id=f"lifecycle_event_{uuid4().hex}",
            lifecycle_id=record.lifecycle_id,
            project_id=record.project_id,
            command_id=command_id,
            actor=actor,
            source_command=source_command,
            occurred_at=datetime.now(UTC),
            from_state=from_state,
            to_state=to_state,
            reviewed_plan_id=record.reviewed_plan_id,
            execution_ticket_id=record.execution_ticket_id,
            recovery_approval_id=record.recovery_approval_id,
            recovery_attempt_id=record.recovery_attempt_id,
            audit_id=record.audit_id,
            run_id=record.run_id,
            observation_id=record.observation_id,
            goal_contract_id=record.goal_contract_id,
            goal_evaluation_id=record.goal_evaluation_id,
            diagnosis_id=record.diagnosis_id,
            recovery_proposal_id=record.recovery_proposal_id,
            reason=reason,
            details=details or {},
        )

    def transition(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        to_state: AgentLifecycleState,
        command_id: str,
        actor: str,
        source_command: str,
        reason: str | None = None,
        updates: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> AgentLifecycleRecord:
        current = self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if to_state not in _TRANSITIONS[current.state]:
            raise SafetyError(
                f"LIFECYCLE_TRANSITION_INVALID: {current.state} -> {to_state}",
                code="LIFECYCLE_TRANSITION_INVALID",
            )
        changes = dict(updates or {})
        for field in (
            "reviewed_plan_id",
            "execution_ticket_id",
            "audit_id",
            "run_id",
            "goal_contract_id",
            "goal_contract_hash",
        ):
            old = getattr(current, field)
            new = changes.get(field)
            recovery_rebind = (
                to_state == "RECOVERING"
                and field in {"execution_ticket_id", "audit_id", "run_id"}
                and changes.get("parent_execution_ticket_id", current.execution_ticket_id)
                    == current.execution_ticket_id
                and changes.get("parent_run_id", current.run_id) == current.run_id
            )
            if old and new and old != new and to_state != "PLAN_DRAFTED" and not recovery_rebind:
                raise SafetyError("LIFECYCLE_BINDING_DRIFT", code="LIFECYCLE_BINDING_DRIFT")
        if to_state in {"EXECUTION_READY", "RUNNING", "RETRYING"}:
            ticket_id = str(changes.get("execution_ticket_id") or current.execution_ticket_id or "")
            ticket = self.store.get_execution_ticket(ticket_id) if ticket_id else None
            if ticket is None or ticket.project_id != project_id:
                raise SafetyError("LIFECYCLE_TICKET_REQUIRED", code="LIFECYCLE_TICKET_REQUIRED")
            reviewed_plan_id = str(changes.get("reviewed_plan_id") or current.reviewed_plan_id or "")
            if ticket.reviewed_plan_id != reviewed_plan_id:
                raise SafetyError("LIFECYCLE_TICKET_PLAN_MISMATCH", code="LIFECYCLE_TICKET_PLAN_MISMATCH")
            if to_state in {"RUNNING", "RETRYING"} and ticket.status != "consumed":
                raise SafetyError("LIFECYCLE_TICKET_NOT_CONSUMED", code="LIFECYCLE_TICKET_NOT_CONSUMED")
        now = datetime.now(UTC)
        updated = current.model_copy(
            update={
                **changes,
                "state": to_state,
                "updated_at": now,
                "last_command_id": command_id,
            }
        )
        event = self._event(
            record=updated,
            command_id=command_id,
            actor=actor,
            source_command=source_command,
            from_state=current.state,
            to_state=to_state,
            reason=reason,
            details=details,
        )
        try:
            return self.store.transition_agent_lifecycle(
                updated,
                event,
                expected_state=current.state,
            )
        except sqlite3.IntegrityError as exc:
            raise SafetyError("LIFECYCLE_COMMAND_REPLAYED", code="LIFECYCLE_COMMAND_REPLAYED") from exc
        except RuntimeError as exc:
            raise StateStoreError("LIFECYCLE_CONCURRENT_TRANSITION") from exc

    def prepare_reviewed_execution(
        self,
        *,
        project_id: str,
        reviewed_plan_id: str,
        execution_ticket_id: str,
        audit_id: str,
        actor: str,
    ) -> AgentLifecycleRecord:
        record = self.create(
            project_id=project_id,
            command_id=f"prepare:{execution_ticket_id}:create",
            actor=actor,
        )
        reviewed_plan = self.store.get_reviewed_plan(reviewed_plan_id)
        goal_contract = (
            reviewed_plan.payload.get("goal_contract")
            if reviewed_plan is not None and isinstance(reviewed_plan.payload, dict)
            else None
        )
        goal_updates: dict[str, Any] = {}
        if isinstance(goal_contract, dict):
            goal_updates = {
                "goal_contract_id": str(goal_contract.get("goal_contract_id") or "") or None,
                "goal_contract_hash": str(goal_contract.get("goal_contract_hash") or "") or None,
            }
        sequence: tuple[AgentLifecycleState, ...] = (
            "CONTEXT_READY",
            "PLAN_DRAFTED",
            "PLAN_VALIDATED",
            "WAITING_FOR_APPROVAL",
            "APPROVED",
            "EXECUTION_READY",
        )
        for index, state in enumerate(sequence, start=1):
            updates: dict[str, Any] = {}
            if state in {"PLAN_DRAFTED", "PLAN_VALIDATED", "WAITING_FOR_APPROVAL", "APPROVED", "EXECUTION_READY"}:
                updates["reviewed_plan_id"] = reviewed_plan_id
                updates.update(goal_updates)
            if state == "EXECUTION_READY":
                ticket = self.store.get_execution_ticket(execution_ticket_id)
                updates.update(
                    execution_ticket_id=execution_ticket_id,
                    audit_id=audit_id,
                    retry_quota=(ticket.retry_policy.max_retry_count if ticket else 0),
                )
            record = self.transition(
                project_id=project_id,
                lifecycle_id=record.lifecycle_id,
                to_state=state,
                command_id=f"prepare:{execution_ticket_id}:{index}:{state}",
                actor=actor,
                source_command="execute_reviewed_preflight",
                updates=updates,
            )
        return record

    def dispatch_execution(
        self,
        *,
        lifecycle: AgentLifecycleRecord,
        actor: str,
        dispatch: Callable[[], tuple[dict[str, Any], ExecutionTicket]],
    ) -> tuple[dict[str, Any], ExecutionTicket, AgentLifecycleRecord]:
        if lifecycle.state != "EXECUTION_READY":
            raise SafetyError("LIFECYCLE_EXECUTION_NOT_READY", code="LIFECYCLE_EXECUTION_NOT_READY")
        try:
            result, consumed = dispatch()
        except Exception as exc:
            self.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="FAILED",
                command_id=f"dispatch:{lifecycle.lifecycle_id}:failed",
                actor=actor,
                source_command="gateway_rejected",
                reason=str(exc),
                updates={"last_error": str(exc)},
            )
            raise
        run_id = str(result.get("run_id") or "")
        running = self.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="RUNNING",
            command_id=f"dispatch:{consumed.execution_ticket_id}:submitted",
            actor=actor,
            source_command="gateway_submitted",
            updates={"run_id": run_id, "execution_ticket_id": consumed.execution_ticket_id},
            details={"executor_status": str(result.get("status") or "UNKNOWN")},
        )
        return result, consumed, running

    def observe(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        command_id: str,
        actor: str,
        previous_observation_id: str | None = None,
        recovery_attempt_id: str | None = None,
    ) -> AgentLifecycleRecord:
        current = self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if current.state not in {"RUNNING", "RETRYING", "RECOVERING"}:
            raise SafetyError("LIFECYCLE_OBSERVATION_NOT_ALLOWED", code="LIFECYCLE_OBSERVATION_NOT_ALLOWED")
        observation = ObservationCollector(self.store).collect(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            previous_observation_id=previous_observation_id,
            recovery_attempt_id=recovery_attempt_id,
        )
        return self.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="OBSERVING",
            command_id=command_id,
            actor=actor,
            source_command="observation_collected",
            updates={
                "observation_id": observation.observation_id,
                "observation_summary": observation.summary(),
                "observation": None,
                "legacy_observation_needs_review": False,
            },
            details={
                "observation_hash": observation.observation_hash,
                "completeness": observation.completeness.status,
            },
        )

    def evaluate_goal(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        command_id: str,
        actor: str,
        previous_goal_evaluation_id: str | None = None,
    ) -> tuple[AgentLifecycleRecord, GoalEvaluationRecord]:
        current = self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if current.state != "OBSERVING" or not current.observation_id:
            raise SafetyError(
                "LIFECYCLE_GOAL_EVALUATION_NOT_ALLOWED",
                code="LIFECYCLE_GOAL_EVALUATION_NOT_ALLOWED",
            )
        evaluating = self.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="EVALUATING",
            command_id=f"{command_id}:evaluating",
            actor=actor,
            source_command="goal_evaluation_started",
        )
        try:
            evaluation = GoalEvaluator(self.store).evaluate(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                observation_id=evaluating.observation_id or "",
                previous_goal_evaluation_id=previous_goal_evaluation_id,
            )
        except Exception as exc:
            self.transition(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                to_state="HUMAN_HANDOFF",
                command_id=f"{command_id}:evaluation-failed",
                actor=actor,
                source_command="goal_evaluation_failed",
                reason=str(exc),
                updates={"last_error": str(exc)},
            )
            raise
        if evaluation.status == "satisfied":
            target: AgentLifecycleState = "GOAL_SATISFIED"
        elif evaluation.status == "not_satisfied":
            target = "DIAGNOSING"
        else:
            target = "HUMAN_HANDOFF"
        completed = self.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state=target,
            command_id=command_id,
            actor=actor,
            source_command="goal_evaluation_completed",
            updates={
                "goal_evaluation_id": evaluation.goal_evaluation_id,
                "goal_evaluation_summary": evaluation.summary(),
            },
            reason=None if target == "GOAL_SATISFIED" else f"Goal evaluation: {evaluation.status}",
            details={
                "goal_evaluation_hash": evaluation.goal_evaluation_hash,
                "status": evaluation.status,
            },
        )
        return completed, evaluation

    def propose_recovery(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        command_id: str,
        actor: str,
        changes: RecoveryChangeRequest | None = None,
        checkpoint: CheckpointEvidence | None = None,
        parent_recovery_proposal_id: str | None = None,
    ) -> tuple[AgentLifecycleRecord, DiagnosisRecord, RecoveryProposal]:
        """Coordinate persistence around the pure proposal engine; never execute."""
        current = self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if current.state == "FAILED":
            current = self.transition(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                to_state="DIAGNOSING",
                command_id=f"{command_id}:diagnosing",
                actor=actor,
                source_command="recovery_diagnosis_started",
            )
        if current.state != "DIAGNOSING":
            raise SafetyError(
                "LIFECYCLE_RECOVERY_PROPOSAL_NOT_ALLOWED",
                code="LIFECYCLE_RECOVERY_PROPOSAL_NOT_ALLOWED",
            )
        observation = self.store.get_observation(current.observation_id or "")
        evaluation = self.store.get_goal_evaluation(current.goal_evaluation_id or "")
        ticket = self.store.get_execution_ticket(current.execution_ticket_id or "")
        reviewed = self.store.get_reviewed_plan(current.reviewed_plan_id or "")
        project = self.store.get_project(project_id)
        if observation is None or evaluation is None or ticket is None or reviewed is None:
            raise SafetyError(
                "RECOVERY_EVIDENCE_REQUIRED",
                code="RECOVERY_EVIDENCE_REQUIRED",
            )
        plan = reviewed.payload.get("plan") if isinstance(reviewed.payload, dict) else None
        if not isinstance(plan, dict):
            raise SafetyError("RECOVERY_REVIEWED_PLAN_REQUIRED", code="RECOVERY_REVIEWED_PLAN_REQUIRED")
        diagnosis = RunDiagnosisService(get_node_contract).build(
            observation=observation,
            evaluation=evaluation,
            ticket=ticket,
        )
        metadata = getattr(project, "metadata", {}) if project is not None else {}
        project_policy = metadata.get("recovery_policy") if isinstance(metadata, dict) else None
        node_attempts = max((node.attempt for node in observation.nodes), default=0)
        subject_node_attempts = max(
            (node.attempt for node in observation.nodes if node.subject_id != "project"),
            default=0,
        )
        proposal = RecoveryProposalEngine(get_node_contract).propose(
            diagnosis=diagnosis,
            plan=plan,
            ticket=ticket,
            project_policy=project_policy,
            usage=RecoveryQuotaUsage(
                lifecycle_recovery_attempts=current.retry_count,
                node_attempts=node_attempts,
                subject_node_attempts=subject_node_attempts,
                replans=0,
                recovery_wall_seconds=0,
            ),
            changes=changes,
            checkpoint=checkpoint,
            parent_recovery_proposal_id=parent_recovery_proposal_id,
        )
        try:
            self.store.add_recovery_diagnosis(diagnosis)
            self.store.add_recovery_proposal(proposal)
        except Exception as exc:
            raise StateStoreError("RECOVERY_PROPOSAL_PERSISTENCE_FAILED") from exc
        summary = proposal.summary()
        target: AgentLifecycleState = (
            "HUMAN_HANDOFF"
            if summary.recommended_action == "HUMAN_HANDOFF"
            else "RECOVERY_PROPOSED"
        )
        updated = self.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state=target,
            command_id=command_id,
            actor=actor,
            source_command="recovery_proposal_created",
            updates={
                "diagnosis_id": diagnosis.diagnosis_id,
                "diagnosis_summary": diagnosis.summary(),
                "recovery_proposal_id": proposal.recovery_proposal_id,
                "recovery_proposal_summary": summary,
            },
            reason=(
                "Recovery requires human handoff"
                if target == "HUMAN_HANDOFF"
                else None
            ),
            details={
                "diagnosis_hash": diagnosis.diagnosis_hash,
                "recovery_proposal_hash": proposal.recovery_proposal_hash,
                "recommended_action": summary.recommended_action,
            },
        )
        return updated, diagnosis, proposal

    def propose_retry(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        command_id: str,
        actor: str,
        node_ids: list[str],
        backend_ids: list[str],
        params: dict[str, Any],
        input_roots: list[str],
        output_roots: list[str],
        classifier: str,
        risk: str,
    ) -> AgentLifecycleRecord:
        current = self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if current.state == "FAILED":
            current = self.transition(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                to_state="DIAGNOSING",
                command_id=f"{command_id}:diagnosing",
                actor=actor,
                source_command="diagnose",
            )
        if current.state != "DIAGNOSING":
            raise SafetyError("LIFECYCLE_RETRY_NOT_ALLOWED", code="LIFECYCLE_RETRY_NOT_ALLOWED")
        ticket = self.store.get_execution_ticket(current.execution_ticket_id or "")
        if ticket is None or current.retry_count >= min(current.retry_quota, ticket.retry_policy.max_retry_count):
            raise SafetyError("LIFECYCLE_RETRY_QUOTA_EXCEEDED", code="LIFECYCLE_RETRY_QUOTA_EXCEEDED")
        changes_contract = (
            not set(node_ids).issubset(ticket.retry_policy.allowed_node_ids)
            or not set(backend_ids).issubset(ticket.approved_backend_ids)
            or set(input_roots) != set(ticket.input_roots)
            or set(output_roots) != set(ticket.output_roots)
        )
        if changes_contract:
            return self.transition(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                to_state="PLAN_DRAFTED",
                command_id=command_id,
                actor=actor,
                source_command="retry_contract_changed",
                reason="Retry changes reviewed nodes, backend, parameters, or path scope",
                updates={
                    "reviewed_plan_id": None,
                    "execution_ticket_id": None,
                    "audit_id": None,
                    "run_id": None,
                    "retry_proposal": None,
                },
            )
        if risk != "low":
            raise SafetyError("LIFECYCLE_RETRY_HIGH_RISK", code="LIFECYCLE_RETRY_HIGH_RISK")
        proposal = RetryProposal(
            proposal_id=f"retry_proposal_{uuid4().hex}",
            node_ids=tuple(sorted(set(node_ids))),
            backend_ids=tuple(sorted(set(backend_ids))),
            parameter_hash=stable_hash(params),
            input_roots=tuple(sorted(input_roots)),
            output_roots=tuple(sorted(output_roots)),
            classifier=classifier,
            risk="low",
            requires_approval=True,
            changes_reviewed_contract=False,
        )
        return self.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="RETRY_PROPOSED",
            command_id=command_id,
            actor=actor,
            source_command="retry_proposed",
            updates={"retry_proposal": proposal},
        )
