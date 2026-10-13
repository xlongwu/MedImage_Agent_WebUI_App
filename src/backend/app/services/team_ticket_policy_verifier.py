"""Fail-closed checks that keep Team advice bound through execution."""

from __future__ import annotations

from src.backend.app.core.config import ConfigService
from src.backend.app.core.config_schema import (
    AgentModelRuntimeConfig,
    AgentMultiAgentConfig,
)
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_team import canonical_hash
from src.backend.app.schemas.planning import PlanningRequest
from src.backend.app.services.agent_multi_agent_policy_service import (
    AgentMultiAgentPolicyService,
    multi_agent_policy_hash,
)
from src.backend.app.services.agent_review_context_projector import context_projector_hash
from src.backend.app.services.agent_review_finding_aggregator import aggregation_policy_hash
from src.backend.app.services.agent_review_role_registry import role_registry_hash
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService
from src.backend.app.services.structured_agent_model_adapter import (
    team_review_model_profile_hash,
)

_TEAM_BINDING_FIELDS = (
    "team_id",
    "team_epoch",
    "base_context_hash",
    "harness_context_hash",
    "advisory_ref",
    "advisory_hash",
    "context_snapshot_hash",
    "context_projector_hash",
    "role_registry_hash",
    "aggregation_policy_hash",
    "team_model_profile_hash",
    "global_policy_hash",
    "consent_epoch",
    "consent_hash",
)


class TeamTicketPolicyVerifier:
    """Rebuild the live Team binding before approval, ticket issue, and dispatch."""

    def __init__(
        self,
        store,
        *,
        multi_agent_config: AgentMultiAgentConfig | None = None,
        model_config: AgentModelRuntimeConfig | None = None,
    ) -> None:
        self.store = store
        current = ConfigService() if multi_agent_config is None or model_config is None else None
        self.multi_agent_config = multi_agent_config or current.multi_agent
        self.model_config = model_config or current.model

    @staticmethod
    def _request(reviewed_plan) -> PlanningRequest | None:
        payload = getattr(reviewed_plan, "payload", None)
        raw = payload.get("planning_request") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return None
        try:
            request = PlanningRequest.model_validate(raw)
        except Exception as exc:
            raise SafetyError(
                "TEAM_TICKET_PLANNING_IDENTITY_INVALID",
                code="TEAM_TICKET_PLANNING_IDENTITY_INVALID",
            ) from exc
        if request.project_id != reviewed_plan.project_id:
            raise SafetyError(
                "TEAM_TICKET_PLANNING_IDENTITY_INVALID",
                code="TEAM_TICKET_PLANNING_IDENTITY_INVALID",
            )
        return request

    @classmethod
    def binding_hash(cls, request: PlanningRequest) -> str | None:
        if request.planning_mode == "single_agent":
            return None
        return canonical_hash({
            "schema_version": 1,
            "planning_mode": request.planning_mode,
            **{field: getattr(request, field) for field in _TEAM_BINDING_FIELDS},
        })

    def verify_reviewed_plan(
        self,
        reviewed_plan,
        *,
        lifecycle_id: str | None = None,
    ) -> str | None:
        request = self._request(reviewed_plan)
        if request is None:
            current_lifecycles = (
                [self.store.get_agent_lifecycle(lifecycle_id)]
                if lifecycle_id is not None
                else self.store.list_agent_lifecycles(reviewed_plan.project_id)
            )
            if any(
                current is not None
                and current.reviewed_plan_id == reviewed_plan.reviewed_plan_id
                and current.planning_mode == "multi_agent"
                for current in current_lifecycles
            ):
                raise SafetyError(
                    "TEAM_TICKET_PLANNING_IDENTITY_INVALID",
                    code="TEAM_TICKET_PLANNING_IDENTITY_INVALID",
                )
            return None
        if request.planning_mode == "single_agent":
            current = self.store.get_agent_lifecycle(request.lifecycle_id)
            if current is not None and current.planning_mode == "multi_agent":
                raise SafetyError(
                    "TEAM_TICKET_LIFECYCLE_BINDING_INVALID",
                    code="TEAM_TICKET_LIFECYCLE_BINDING_INVALID",
                )
            return None
        if (
            not self.multi_agent_config.enabled
            or request.global_policy_hash
            != multi_agent_policy_hash(self.multi_agent_config)
            or lifecycle_id is not None and request.lifecycle_id != lifecycle_id
        ):
            raise SafetyError(
                "TEAM_TICKET_POLICY_CHANGED",
                code="TEAM_TICKET_POLICY_CHANGED",
            )

        lifecycle = self.store.get_agent_lifecycle(request.lifecycle_id)
        if (
            lifecycle is None
            or lifecycle.project_id != request.project_id
            or lifecycle.planning_mode != "multi_agent"
            or lifecycle.planning_team_id != request.team_id
            or lifecycle.planning_team_epoch != request.team_epoch
            or lifecycle.reviewed_plan_id != reviewed_plan.reviewed_plan_id
            or lifecycle.state not in {
                "WAITING_FOR_APPROVAL", "APPROVED", "EXECUTION_READY",
                "RECOVERY_READY", "RECOVERING",
            }
        ):
            raise SafetyError(
                "TEAM_TICKET_LIFECYCLE_BINDING_INVALID",
                code="TEAM_TICKET_LIFECYCLE_BINDING_INVALID",
            )

        team = self.store.get_agent_team(request.project_id, request.team_id or "")
        advisory = self.store.get_agent_team_advisory(
            request.project_id, request.team_id or ""
        )
        context_pair = self.store.get_agent_team_context(
            request.project_id, request.team_id or ""
        )
        base = self.store.get_agent_planning_base_context(
            request.project_id, request.base_context_hash or ""
        )
        harness_context = self.store.get_agent_harness_context(
            request.harness_context_hash or ""
        )
        if team is None or advisory is None or context_pair is None or base is None:
            raise SafetyError(
                "TEAM_TICKET_CONTEXT_BINDING_INVALID",
                code="TEAM_TICKET_CONTEXT_BINDING_INVALID",
            )
        snapshot, _role_contexts = context_pair
        if (
            team.status != "COMPLETED"
            or team.team_id != request.team_id
            or team.team_epoch != request.team_epoch
            or team.project_id != request.project_id
            or team.lifecycle_id != request.lifecycle_id
            or team.advisory_id != request.advisory_ref
            or team.global_policy_hash != request.global_policy_hash
            or advisory.advisory_id != request.advisory_ref
            or advisory.advisory_hash != request.advisory_hash
            or advisory.decision != "accepted"
            or advisory.blocking_codes
            or advisory.conflict_codes
            or advisory.base_context_hash != request.base_context_hash
            or advisory.context_snapshot_hash != request.context_snapshot_hash
            or advisory.context_projector_hash != request.context_projector_hash
            or advisory.role_registry_hash != request.role_registry_hash
            or advisory.aggregation_policy_hash != request.aggregation_policy_hash
            or advisory.model_profile_hash != request.team_model_profile_hash
            or snapshot.snapshot_hash != request.context_snapshot_hash
            or snapshot.team_id != request.team_id
            or snapshot.team_epoch != request.team_epoch
            or snapshot.base_context_hash != request.base_context_hash
            or base.base_context_hash != request.base_context_hash
            or harness_context is None
            or harness_context.context_hash != request.harness_context_hash
            or harness_context.planning_mode != "multi_agent"
            or harness_context.team_id != request.team_id
            or harness_context.team_epoch != request.team_epoch
            or harness_context.base_context_hash != request.base_context_hash
            or harness_context.team_advisory is None
            or harness_context.team_advisory.advisory_hash != request.advisory_hash
            or request.role_registry_hash != role_registry_hash()
            or request.context_projector_hash != context_projector_hash()
            or request.aggregation_policy_hash != aggregation_policy_hash()
            or request.team_model_profile_hash
            != team_review_model_profile_hash(
                config=self.model_config,
                max_output_tokens=self.multi_agent_config.max_output_tokens_per_call,
            )
        ):
            raise SafetyError(
                "TEAM_TICKET_CONTEXT_BINDING_INVALID",
                code="TEAM_TICKET_CONTEXT_BINDING_INVALID",
            )

        consent = ProjectAgentSettingsService(self.store).get(
            project_id=request.project_id
        ).multi_agent_consent
        if (
            not consent.enabled
            or consent.consent_epoch != request.consent_epoch
            or consent.consent_hash != request.consent_hash
        ):
            raise SafetyError(
                "MULTI_AGENT_CONSENT_INVALIDATED",
                code="MULTI_AGENT_CONSENT_INVALIDATED",
            )
        consent = AgentMultiAgentPolicyService(
            self.store, self.multi_agent_config
        ).verify_team_budget(
            project_id=request.project_id,
            max_model_calls=team.budget.max_model_calls,
            reserved_input_tokens=team.budget.reserved_input_tokens,
        )
        if (
            consent.consent_epoch != request.consent_epoch
            or consent.consent_hash != request.consent_hash
            or consent.consent_epoch != team.consent_epoch
            or consent.consent_hash != team.consent_hash
            or advisory.consent_epoch != request.consent_epoch
            or advisory.consent_hash != request.consent_hash
        ):
            raise SafetyError(
                "MULTI_AGENT_CONSENT_INVALIDATED",
                code="MULTI_AGENT_CONSENT_INVALIDATED",
            )
        current_findings = {
            item.finding_id: item.finding_hash
            for item in self.store.list_agent_team_findings(
                request.project_id, request.team_id or ""
            )
        }
        if {
            item.finding_id: item.finding_hash for item in request.team_findings
        } != {
            item_id: current_findings[item_id]
            for item_id in advisory.accepted_finding_ids
            if item_id in current_findings
        } or any(item_id not in current_findings for item_id in advisory.accepted_finding_ids):
            raise SafetyError(
                "TEAM_TICKET_FINDING_BINDING_INVALID",
                code="TEAM_TICKET_FINDING_BINDING_INVALID",
            )
        return self.binding_hash(request)

    def verify_ticket(self, ticket) -> str | None:
        reviewed_plan = self.store.get_reviewed_plan(ticket.reviewed_plan_id)
        if reviewed_plan is None:
            if ticket.team_binding_hash is not None:
                raise SafetyError(
                    "TEAM_TICKET_PLANNING_IDENTITY_INVALID",
                    code="TEAM_TICKET_PLANNING_IDENTITY_INVALID",
                )
            return None
        binding = self.verify_reviewed_plan(reviewed_plan)
        if binding != ticket.team_binding_hash:
            raise SafetyError(
                "TEAM_TICKET_BINDING_MISMATCH",
                code="TEAM_TICKET_BINDING_MISMATCH",
            )
        return binding

    def reconcile_issued_tickets(self) -> int:
        """Revoke stale Team tickets at startup; dispatch still rechecks live state."""
        return sum(
            self.reconcile_project_tickets(project.id)
            for project in self.store.list_projects()
        )

    def reconcile_project_tickets(self, project_id: str) -> int:
        """Revoke stale, issued Team tickets after project consent changes."""
        from src.backend.app.services.execution_ticket_service import ExecutionTicketService

        ticket_service = ExecutionTicketService(self.store)
        revoked = 0
        for ticket in self.store.list_execution_tickets(project_id):
            if ticket.status != "issued":
                continue
            try:
                self.verify_ticket(ticket)
            except SafetyError as exc:
                ticket_service.revoke(
                    ticket.execution_ticket_id,
                    reason=str(exc.code or "TEAM_TICKET_POLICY_CHANGED"),
                )
                revoked += 1
        return revoked
