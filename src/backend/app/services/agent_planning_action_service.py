"""The two deterministic business actions available to the Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import uuid4

from src.backend.app.schemas.agent_harness import DraftPlanAction, RequestDecisionAction
from src.backend.app.schemas.agent_lifecycle import PendingDecisionBatch
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.services.agent_orchestrator import AgentOrchestrator


@dataclass(frozen=True)
class HarnessActionResult:
    lifecycle: object
    attempt_status: str
    terminal_reason: str | None
    action_result_code: str | None = None
    action_already_applied: bool = False


class AgentPlanningActionConflictError(RuntimeError):
    code = "AGENT_HARNESS_ACTION_STATE_CONFLICT"


class AgentPlanningActionService:
    """Apply only typed planning actions; it has no execution dependencies."""

    _PLANNING_STATES = frozenset({"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"})

    def __init__(
        self,
        store,
        *,
        draft_plan: Callable[..., object],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.draft_plan = draft_plan
        self.now = now or (lambda: datetime.now(UTC))
        self.orchestrator = AgentOrchestrator(store)

    def apply(
        self,
        *,
        lifecycle_id: str,
        action: RequestDecisionAction | DraftPlanAction,
        actor: str,
        action_record=None,
    ) -> HarnessActionResult:
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        if lifecycle is None or lifecycle.state != action.expected_state or lifecycle.state not in self._PLANNING_STATES:
            raise AgentPlanningActionConflictError(AgentPlanningActionConflictError.code)
        if isinstance(action, DraftPlanAction):
            result = self.draft_plan(
                lifecycle=lifecycle,
                command_id=f"harness:{lifecycle.lifecycle_id}:draft-plan",
                actor=actor,
            )
            status = "WAITING_FOR_USER" if result.state in {
                "WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"
            } else "READY"
            if action_record is not None:
                reviewed = self.store.get_reviewed_plan(result.reviewed_plan_id) if result.reviewed_plan_id else None
                pending = result.pending_decision_batch
                applied_action = action_record.model_copy(update={
                    "status": "applied",
                    "decision_batch_id": pending.batch_id if pending else None,
                    "decision_batch_hash": stable_hash(pending.model_dump(mode="json")) if pending else None,
                    "reviewed_plan_id": reviewed.reviewed_plan_id if reviewed else None,
                    "reviewed_plan_hash": reviewed.plan_hash if reviewed else None,
                    "completed_at": self.now(),
                })
                self.store.update_agent_harness_action(applied_action, expected_status="accepted")
                return HarnessActionResult(result, status, None, action_already_applied=True)
            return HarnessActionResult(result, status, None)

        if action_record is None or not action_record.context_hash or not action_record.evidence_snapshot_hash:
            raise RuntimeError("AGENT_HARNESS_ACTION_SOURCE_MISSING")
        decision = PendingDecisionBatch(
            batch_id=f"harness_decision_batch_{uuid4().hex}",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            evidence_snapshot_hash=action_record.evidence_snapshot_hash,
            items=(action.decision,),
            expires_at=self.now() + timedelta(hours=24),
            source="harness",
        )
        state = (
            "WAITING_FOR_INPUT"
            if action.decision.kind in {"missing_input", "goal_revision"}
            else "WAITING_FOR_SCIENCE_DECISION"
        )
        if lifecycle.state == "CREATED" and state == "WAITING_FOR_SCIENCE_DECISION":
            lifecycle = self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="CONTEXT_READY",
                command_id=f"harness:{lifecycle.lifecycle_id}:context",
                actor=actor,
                source_command="harness_context_ready",
            )
        current = self.store.get_agent_lifecycle(lifecycle.lifecycle_id)
        if current is None:
            raise AgentPlanningActionConflictError(AgentPlanningActionConflictError.code)
        context = dict(current.command_context)
        context["evidence_snapshot_hash"] = action_record.evidence_snapshot_hash
        context["harness_evidence_purpose"] = action_record.context_purpose
        command_id = f"harness:{current.lifecycle_id}:decision:{decision.batch_id}"
        result = current.model_copy(update={
            "state": state,
            "pending_decision_batch": decision,
            "evidence_snapshot_hash": action_record.evidence_snapshot_hash,
            "command_context": context,
            "updated_at": self.now(),
            "last_command_id": command_id,
        })
        event = self.orchestrator._event(
            record=result, command_id=command_id, actor=actor,
            source_command="harness_decision_required", from_state=current.state,
            to_state=state, reason=action.decision.impact,
            details={"action_id": action_record.action_id, "context_hash": action_record.context_hash},
        )
        applied_action = action_record.model_copy(update={
            "status": "applied", "decision_batch_id": decision.batch_id,
            "decision_batch_hash": stable_hash(decision.model_dump(mode="json")),
            "completed_at": self.now(),
        })
        transition = getattr(self.store, "transition_agent_lifecycle_with_harness_action", None)
        if not callable(transition):
            raise RuntimeError("AGENT_HARNESS_ACTION_TRANSACTION_UNAVAILABLE")
        transition(result, event, applied_action, expected_state=current.state, expected_action_status="accepted")
        return HarnessActionResult(result, "WAITING_FOR_USER", None, action_already_applied=True)
