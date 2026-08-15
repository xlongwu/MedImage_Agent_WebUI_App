"""The two deterministic business actions available to the Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import uuid4

from src.backend.app.schemas.agent_harness import DraftPlanAction, RequestDecisionAction
from src.backend.app.schemas.agent_lifecycle import PendingDecisionBatch
from src.backend.app.services.agent_orchestrator import AgentOrchestrator


@dataclass(frozen=True)
class HarnessActionResult:
    lifecycle: object
    attempt_status: str
    terminal_reason: str | None
    action_result_code: str | None = None


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
    ) -> HarnessActionResult:
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        if lifecycle is None or lifecycle.state != action.expected_state or lifecycle.state not in self._PLANNING_STATES:
            raise AgentPlanningActionConflictError(self.code)
        if isinstance(action, DraftPlanAction):
            result = self.draft_plan(
                lifecycle=lifecycle,
                command_id=f"harness:{lifecycle.lifecycle_id}:draft-plan",
                actor=actor,
            )
            status = "WAITING_FOR_USER" if result.state in {
                "WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"
            } else "READY"
            return HarnessActionResult(result, status, None)

        decision = PendingDecisionBatch(
            batch_id=f"harness_decision_batch_{uuid4().hex}",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            evidence_snapshot_hash=str(
                (lifecycle.command_context or {}).get("evidence_snapshot_hash")
                or "harness-evidence-unavailable"
            ),
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
        result = self.orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state=state,
            command_id=f"harness:{lifecycle.lifecycle_id}:decision:{decision.batch_id}",
            actor=actor,
            source_command="harness_decision_required",
            updates={"pending_decision_batch": decision},
            reason=action.decision.impact,
        )
        return HarnessActionResult(result, "WAITING_FOR_USER", None)
