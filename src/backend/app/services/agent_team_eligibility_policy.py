"""Deterministic eligibility rules for explicitly requested review Teams."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.backend.app.schemas.agent_team import Sha256, canonical_hash

EligibilityReason = Literal[
    "MULTI_AGENT_ELIGIBLE",
    "MULTI_AGENT_TASK_KIND_UNSUPPORTED",
    "MULTI_AGENT_CONTEXT_UNSAFE_TO_SPLIT",
    "MULTI_AGENT_PROVIDER_UNAVAILABLE",
    "MULTI_AGENT_PROJECT_CONSENT_REQUIRED",
    "MULTI_AGENT_EVIDENCE_DOMAINS_INSUFFICIENT",
]


class AgentTeamEligibilityInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(min_length=1, max_length=128)
    task_kind: Literal["planning", "status_query", "ui_command", "other"]
    independent_evidence_domains: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    has_science_decision: bool = False
    has_path_or_scope_risk: bool = False
    has_competing_interpretations: bool = False
    context_is_role_splittable: bool = False
    provider_allowed: bool = False
    project_consent_enabled: bool = False
    project_consent_epoch: int = Field(ge=0)
    project_consent_hash: Sha256


class AgentTeamEligibilityDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    reason_code: EligibilityReason
    input_hash: str = Field(min_length=64, max_length=64)


class AgentTeamEligibilityPolicy:
    """Use only structured, parent-owned facts; never infer from free model text."""

    def evaluate(self, value: AgentTeamEligibilityInput) -> AgentTeamEligibilityDecision:
        if value.task_kind != "planning":
            reason: EligibilityReason = "MULTI_AGENT_TASK_KIND_UNSUPPORTED"
        elif not value.provider_allowed:
            reason = "MULTI_AGENT_PROVIDER_UNAVAILABLE"
        elif not value.project_consent_enabled:
            reason = "MULTI_AGENT_PROJECT_CONSENT_REQUIRED"
        elif not value.context_is_role_splittable:
            reason = "MULTI_AGENT_CONTEXT_UNSAFE_TO_SPLIT"
        elif (
            len(set(value.independent_evidence_domains)) >= 2
            or value.has_science_decision and value.has_path_or_scope_risk
            or value.has_competing_interpretations
        ):
            reason = "MULTI_AGENT_ELIGIBLE"
        else:
            reason = "MULTI_AGENT_EVIDENCE_DOMAINS_INSUFFICIENT"
        return AgentTeamEligibilityDecision(
            eligible=reason == "MULTI_AGENT_ELIGIBLE",
            reason_code=reason,
            input_hash=canonical_hash(value.model_dump(mode="json")),
        )
