"""Build the three fixed, consent-bound read-only Team role contexts."""

from __future__ import annotations

from src.backend.app.schemas.agent_eval import AgentReviewerKind
from src.backend.app.schemas.agent_harness import AgentPlanningBaseContext, AgentTeamRoleContext
from src.backend.app.schemas.agent_team import (
    AgentTeamBudgetSlice,
    AgentTeamContextSnapshot,
    AgentTeamRecord,
    agent_team_context_snapshot_hash,
)
from src.backend.app.schemas.project_agent_settings import ProjectMultiAgentConsent
from src.backend.app.services.agent_review_context_projector import (
    AgentReviewContextProjector,
    context_projector_hash,
)
from src.backend.app.services.agent_review_role_registry import role_for, role_registry_hash


class AgentTeamContextService:
    """Project only the frozen Base sections allowed for each fixed reviewer."""

    REVIEWER_KINDS: tuple[AgentReviewerKind, ...] = (
        "science",
        "safety",
        "completeness",
    )

    def __init__(self, projector: AgentReviewContextProjector | None = None) -> None:
        self.projector = projector or AgentReviewContextProjector()

    def project_roles(
        self,
        *,
        base_context: AgentPlanningBaseContext,
        team: AgentTeamRecord,
        consent: ProjectMultiAgentConsent,
        budget_slices: dict[AgentReviewerKind, AgentTeamBudgetSlice],
    ) -> dict[AgentReviewerKind, AgentTeamRoleContext]:
        if (
            not base_context.complete
            or base_context.project_id != team.project_id
            or base_context.lifecycle_id != team.lifecycle_id
            or team.role_registry_hash != role_registry_hash()
            or team.context_projector_hash != context_projector_hash()
            or team.consent_epoch != consent.consent_epoch
            or team.consent_hash != consent.consent_hash
            or set(budget_slices) != set(self.REVIEWER_KINDS)
        ):
            raise ValueError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        return self._project_roles(
            base_context=base_context,
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            model_profile_hash=team.model_profile_hash,
            consent=consent,
            budget_slices=budget_slices,
        )

    def project_snapshot(
        self,
        *,
        base_context: AgentPlanningBaseContext,
        team_id: str,
        team_epoch: int,
        project_id: str,
        lifecycle_id: str,
        model_profile_hash: str,
        consent: ProjectMultiAgentConsent,
        budget_slices: dict[AgentReviewerKind, AgentTeamBudgetSlice],
    ) -> tuple[AgentTeamContextSnapshot, dict[AgentReviewerKind, AgentTeamRoleContext]]:
        """Build the frozen Base reference and role projection identities for bundle creation."""
        if (
            base_context.project_id != project_id
            or base_context.lifecycle_id != lifecycle_id
            or not consent.enabled
            or consent.project_id != project_id
            or set(budget_slices) != set(self.REVIEWER_KINDS)
        ):
            raise ValueError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        roles = self._project_roles(
            base_context=base_context,
            team_id=team_id,
            team_epoch=team_epoch,
            model_profile_hash=model_profile_hash,
            consent=consent,
            budget_slices=budget_slices,
        )
        schema_versions = {role.output_schema_version for role in roles.values()}
        if len(schema_versions) != 1:
            raise ValueError("TEAM_ROLE_REGISTRY_INVALID")
        payload = {
            "schema_version": 1,
            "team_id": team_id,
            "team_epoch": team_epoch,
            "project_id": project_id,
            "lifecycle_id": lifecycle_id,
            "base_context_hash": base_context.base_context_hash,
            "role_context_hashes": {
                kind: role.role_context_hash for kind, role in roles.items()
            },
            "consent_epoch": consent.consent_epoch,
            "consent_hash": consent.consent_hash,
            "role_registry_hash": role_registry_hash(),
            "context_projector_hash": context_projector_hash(),
            "policy_version": base_context.policy_version,
            "redaction_policy_version": base_context.redaction_policy_version,
            "prompt_template_version": "+".join(
                sorted({role_for(kind).prompt_template_version for kind in roles})
            ),
            "output_schema_version": next(iter(schema_versions)),
        }
        snapshot = AgentTeamContextSnapshot(
            snapshot_hash=agent_team_context_snapshot_hash(payload), **payload
        )
        return snapshot, roles

    def _project_roles(
        self,
        *,
        base_context: AgentPlanningBaseContext,
        team_id: str,
        team_epoch: int,
        model_profile_hash: str,
        consent: ProjectMultiAgentConsent,
        budget_slices: dict[AgentReviewerKind, AgentTeamBudgetSlice],
    ) -> dict[AgentReviewerKind, AgentTeamRoleContext]:
        return {
            kind: self.projector.project_base_context(
                base_context=base_context,
                team_id=team_id,
                team_epoch=team_epoch,
                reviewer_kind=kind,
                consent=consent,
                model_profile_hash=model_profile_hash,
                budget_slice=budget_slices[kind],
                deadline_at=budget_slices[kind].deadline_at,
            )
            for kind in self.REVIEWER_KINDS
        }
