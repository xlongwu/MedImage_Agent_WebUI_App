"""Cancellation and explicit recovery commands for Agent Tasks."""

from __future__ import annotations

import logging
from typing import Callable

from src.backend.app.core.agent_logging import agent_log_context
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.recovery_execution_service import RecoveryExecutionService


logger = logging.getLogger(__name__)


class AgentRecoveryCommandService:
    def __init__(
        self, store, *, stop_planning: Callable[..., object] | None = None,
        recovery_execution_factory: Callable[[object], object] = RecoveryExecutionService,
    ) -> None:
        self.store = store
        self.orchestrator = AgentOrchestrator(store)
        self.stop_planning = stop_planning
        self.recovery_execution_factory = recovery_execution_factory

    def cancel(self, *, project_id: str, lifecycle_id: str, command_id: str, actor: str, reason: str | None = None):
        canceled = self.orchestrator.cancel(
            project_id=project_id, lifecycle_id=lifecycle_id, command_id=command_id,
            actor=actor, reason=reason,
        )
        if self.stop_planning is not None:
            self.stop_planning(lifecycle_id=lifecycle_id, reason="LIFECYCLE_CANCELED")
        return canceled

    def approve_recovery(self, *, project_id: str, lifecycle_id: str, command_id: str, actor: str):
        current = self.orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if current.state != "RECOVERY_PROPOSED" or not current.recovery_proposal_id:
            raise SafetyError("RECOVERY_APPROVAL_STATE_INVALID", code="RECOVERY_APPROVAL_STATE_INVALID")
        proposal = self.store.get_recovery_proposal(current.recovery_proposal_id)
        if proposal is None or proposal.bindings.project_id != project_id or proposal.bindings.lifecycle_id != lifecycle_id:
            raise SafetyError("RECOVERY_PROPOSAL_NOT_FOUND", code="RECOVERY_PROPOSAL_NOT_FOUND")
        candidate = next((item for item in proposal.candidates if item.candidate_id == proposal.recommended_candidate_id and item.eligible), None)
        if candidate is None:
            raise SafetyError("RECOVERY_CANDIDATE_NOT_ELIGIBLE", code="RECOVERY_CANDIDATE_NOT_ELIGIBLE")
        recovery = self.recovery_execution_factory(self.store)
        recovery.approve(
            project_id=project_id, lifecycle_id=lifecycle_id,
            proposal_id=proposal.recovery_proposal_id, candidate_id=candidate.candidate_id,
            command_id=f"{command_id}:approve", actor=actor,
        )
        lifecycle, _attempt, _result = recovery.execute(
            project_id=project_id, lifecycle_id=lifecycle_id,
            proposal_id=proposal.recovery_proposal_id, candidate_id=candidate.candidate_id,
            command_id=command_id, actor=actor,
        )
        logger.info(
            "agent_recovery_approved",
            extra={"medimage": agent_log_context(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                reviewed_plan_id=lifecycle.reviewed_plan_id,
                execution_ticket_id=lifecycle.execution_ticket_id,
                run_id=lifecycle.run_id,
                event_code="AGENT_RECOVERY_APPROVED",
            )},
        )
        return lifecycle
