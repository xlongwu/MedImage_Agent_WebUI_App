"""Bounded, versioned input contract for every Agent planning attempt."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.backend.app.schemas.agent_team import (
    AgentTeamAdvisory,
    AgentTeamFinding,
    Sha256,
)

RevisionReason = Literal[
    "template_input_registered",
    "initial",
    "decision_answered",
    "goal_revised",
    "recovery_replan",
]


class PlanningRequest(BaseModel):
    """All planner inputs frozen before candidate-plan generation.

    This contract deliberately contains only already-bound, structured values.
    Planner implementations must not reach into a memory database, rawdata, or
    frontend-local state to supplement it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    project_id: str
    lifecycle_id: str
    goal: str
    project_config_path: str
    evidence_snapshot_hash: str
    science_answers: dict[str, str] = Field(default_factory=dict)
    memory_context_hash: str | None = None
    memory_context_refs: tuple[dict[str, Any], ...] = ()
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: RevisionReason
    recovery_proposal_hash: str | None = None
    recovery_candidate_hash: str | None = None
    provider_ref: str
    prompt_version: str
    model_profile_hash: str = Field(min_length=64, max_length=64)
    planning_mode: Literal["single_agent", "multi_agent"] = "single_agent"
    team_id: str | None = Field(default=None, min_length=1, max_length=128)
    team_epoch: int | None = Field(default=None, ge=1)
    base_context_hash: Sha256 | None = None
    harness_context_hash: Sha256 | None = None
    advisory_ref: str | None = Field(default=None, min_length=1, max_length=128)
    advisory_hash: Sha256 | None = None
    context_snapshot_hash: Sha256 | None = None
    context_projector_hash: Sha256 | None = None
    role_registry_hash: Sha256 | None = None
    aggregation_policy_hash: Sha256 | None = None
    team_model_profile_hash: Sha256 | None = None
    global_policy_hash: Sha256 | None = None
    consent_epoch: int | None = Field(default=None, ge=0)
    consent_hash: Sha256 | None = None
    team_advisory: AgentTeamAdvisory | None = None
    team_findings: tuple[AgentTeamFinding, ...] = ()

    @model_validator(mode="after")
    def validate_team_binding(self) -> PlanningRequest:
        team_values = (
            self.team_id,
            self.team_epoch,
            self.base_context_hash,
            self.harness_context_hash,
            self.advisory_ref,
            self.advisory_hash,
            self.context_snapshot_hash,
            self.context_projector_hash,
            self.role_registry_hash,
            self.aggregation_policy_hash,
            self.team_model_profile_hash,
            self.global_policy_hash,
            self.consent_epoch,
            self.consent_hash,
        )
        if self.planning_mode == "single_agent":
            if any(value is not None for value in team_values) or self.team_advisory is not None or self.team_findings:
                raise ValueError("PLANNING_REQUEST_TEAM_BINDING_INVALID")
            return self
        advisory = self.team_advisory
        if (
            any(value is None for value in team_values)
            or advisory is None
            or advisory.decision != "accepted"
            or advisory.blocking_codes
            or advisory.conflict_codes
            or self.team_epoch is None
            or self.consent_epoch is None
            or self.team_id != advisory.team_id
            or self.team_epoch != advisory.team_epoch
            or self.project_id != advisory.project_id
            or self.lifecycle_id != advisory.lifecycle_id
            or self.base_context_hash != advisory.base_context_hash
            or self.advisory_ref != advisory.advisory_id
            or self.advisory_hash != advisory.advisory_hash
            or self.context_snapshot_hash != advisory.context_snapshot_hash
            or self.context_projector_hash != advisory.context_projector_hash
            or self.role_registry_hash != advisory.role_registry_hash
            or self.aggregation_policy_hash != advisory.aggregation_policy_hash
            or self.team_model_profile_hash != advisory.model_profile_hash
            or self.consent_epoch != advisory.consent_epoch
            or self.consent_hash != advisory.consent_hash
            or {item.finding_id for item in self.team_findings} != set(advisory.accepted_finding_ids)
            or any(
                item.team_id != advisory.team_id
                or item.team_epoch != advisory.team_epoch
                or item.project_id != advisory.project_id
                or item.lifecycle_id != advisory.lifecycle_id
                for item in self.team_findings
            )
        ):
            raise ValueError("PLANNING_REQUEST_TEAM_BINDING_INVALID")
        return self

    def identity_payload(self) -> dict[str, Any]:
        """Return the exact data that can affect a reviewed-plan identity."""
        payload = self.model_dump(mode="json")
        if self.planning_mode == "single_agent":
            for name in (
                "planning_mode", "team_id", "team_epoch", "base_context_hash",
                "harness_context_hash", "advisory_ref", "advisory_hash",
                "context_snapshot_hash", "context_projector_hash", "role_registry_hash",
                "aggregation_policy_hash", "team_model_profile_hash", "global_policy_hash", "consent_epoch",
                "consent_hash", "team_advisory", "team_findings",
            ):
                payload.pop(name)
        return payload

    def planner_constraints(self) -> dict[str, Any]:
        """Expose only confirmed decision answers to the provider adapter."""
        constraints: dict[str, Any] = {"science_answers": dict(self.science_answers)}
        if self.planning_mode == "multi_agent" and self.team_advisory is not None:
            constraints["team_review"] = {
                "team_id": self.team_id,
                "team_epoch": self.team_epoch,
                "advisory_hash": self.advisory_hash,
                "accepted_findings": [
                    {
                        "reviewer_kind": item.reviewer_kind,
                        "severity": item.severity,
                        "code": item.code,
                        "message_key": item.message_key,
                        "input_refs": list(item.input_refs),
                    }
                    for item in self.team_findings
                ],
            }
        return constraints
