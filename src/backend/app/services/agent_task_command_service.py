"""Thin command entrypoint for the persisted Agent Task lifecycle."""

from __future__ import annotations

from src.backend.app.services.agent_approval_execution_service import (
    AgentApprovalExecutionService,
)
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.services.agent_recovery_command_service import (
    AgentRecoveryCommandService,
)


class AgentTaskCommandService:
    """Validate command replay at the boundary and delegate domain work.

    Dependency construction belongs in ``api.dependencies``.  This class owns
    neither configuration, storage construction, models, nor background work.
    """

    def __init__(
        self,
        store,
        *,
        planning_service: AgentPlanningService,
        approval_execution_service: AgentApprovalExecutionService,
        recovery_command_service: AgentRecoveryCommandService,
    ) -> None:
        self.store = store
        self.planning_service = planning_service
        self.approval_execution_service = approval_execution_service
        self.recovery_command_service = recovery_command_service

    def create(self, *, project_id: str, goal: str, command_id: str, actor: str):
        return self.planning_service.create(
            project_id=project_id, goal=goal, command_id=command_id, actor=actor
        )

    def answer(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        batch_id: str,
        answers: tuple | list,
        command_id: str,
        actor: str,
    ):
        return self.planning_service.answer(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            batch_id=batch_id,
            answers=answers,
            command_id=command_id,
            actor=actor,
        )

    def approve(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        approval_summary_hash: str,
        command_id: str,
        actor: str,
    ):
        return self.approval_execution_service.approve(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            approval_summary_hash=approval_summary_hash,
            command_id=command_id,
            actor=actor,
        )

    def cancel(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        command_id: str,
        actor: str,
        reason: str | None = None,
    ):
        return self.recovery_command_service.cancel(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            command_id=command_id,
            actor=actor,
            reason=reason,
        )

    def approve_recovery(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        command_id: str,
        actor: str,
    ):
        return self.recovery_command_service.approve_recovery(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            command_id=command_id,
            actor=actor,
        )
