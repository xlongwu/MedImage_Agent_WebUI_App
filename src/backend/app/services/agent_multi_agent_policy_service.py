"""Fail-closed global and project consent checks for planning reviewers."""

from __future__ import annotations

from src.backend.app.core.config_schema import (
    AgentMultiAgentConfig,
    multi_agent_policy_hash,
)
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.project_agent_settings import ProjectMultiAgentConsent
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService


class AgentMultiAgentPolicyService:
    """Check the non-execution gates before a reviewer provider call."""

    def __init__(self, store, config: AgentMultiAgentConfig) -> None:
        self.store = store
        self.config = config

    def verify_team_budget(
        self,
        *,
        project_id: str,
        max_model_calls: int,
        reserved_input_tokens: int,
    ) -> ProjectMultiAgentConsent:
        if not self.config.enabled:
            raise SafetyError("MULTI_AGENT_DISABLED", code="MULTI_AGENT_DISABLED")
        consent = self._current_consent(project_id)
        if not consent.enabled:
            raise SafetyError(
                "MULTI_AGENT_PROJECT_CONSENT_REQUIRED",
                code="MULTI_AGENT_PROJECT_CONSENT_REQUIRED",
            )
        if (
            max_model_calls < 3
            or max_model_calls > min(self.config.max_model_calls, consent.max_model_calls)
            or reserved_input_tokens < 1
            or reserved_input_tokens
            > min(self.config.max_input_tokens, consent.max_input_tokens)
        ):
            raise SafetyError("MULTI_AGENT_BUDGET_EXCEEDED", code="MULTI_AGENT_BUDGET_EXCEEDED")
        return consent

    def preflight_provider_call(
        self,
        *,
        project_id: str,
        expected_consent_epoch: int,
        expected_consent_hash: str,
        expected_global_policy_hash: str | None = None,
        reserved_input_tokens: int,
    ) -> ProjectMultiAgentConsent:
        """Re-read consent immediately before handing a request to an adapter."""
        if not self.config.enabled:
            raise SafetyError("MULTI_AGENT_DISABLED", code="MULTI_AGENT_DISABLED")
        if (
            expected_global_policy_hash is not None
            and expected_global_policy_hash != multi_agent_policy_hash(self.config)
        ):
            raise SafetyError(
                "MULTI_AGENT_POLICY_CHANGED", code="MULTI_AGENT_POLICY_CHANGED"
            )
        consent = self._current_consent(project_id)
        if (
            not consent.enabled
            or consent.consent_epoch != expected_consent_epoch
            or consent.consent_hash != expected_consent_hash
        ):
            raise SafetyError(
                "MULTI_AGENT_CONSENT_INVALIDATED",
                code="MULTI_AGENT_CONSENT_INVALIDATED",
            )
        if (
            reserved_input_tokens < 1
            or reserved_input_tokens
            > min(self.config.max_input_tokens, consent.max_input_tokens)
        ):
            raise SafetyError("MULTI_AGENT_BUDGET_EXCEEDED", code="MULTI_AGENT_BUDGET_EXCEEDED")
        return consent

    def _current_consent(self, project_id: str) -> ProjectMultiAgentConsent:
        return ProjectAgentSettingsService(self.store).get(
            project_id=project_id
        ).multi_agent_consent
