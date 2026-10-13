"""Persisted, bounded control-plane records for the optional Agent Harness.

The models in this module intentionally describe *advice* and its audit trail.
They carry no execution ticket, approval, runner, filesystem, or shell fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from src.backend.app.agent_skills.schemas import SkillContextRef
from src.backend.app.schemas.agent_lifecycle import DecisionItem
from src.backend.app.schemas.agent_team import (
    AgentPlanningMode,
    AgentTeamAdvisory,
    AgentTeamBudgetSlice,
    Sha256,
    canonical_hash,
)

AgentHarnessStatus = Literal[
    "READY",
    "RUNNING",
    "WAITING_FOR_USER",
    "FINISHED",
    "STOPPED",
    "FAILED",
]
AgentHarnessActionKind = Literal["request_decision", "draft_plan"]
AgentHarnessContextSectionName = Literal[
    "goal",
    "policy",
    "project_evidence",
    "decision_state",
    "plan_state",
    "execution_state",
    "latest_observation",
    "last_action_result",
    "memory_context",
    "budget",
]
AgentHarnessContextV4SectionName = AgentHarnessContextSectionName | Literal["team_advisory"]
AgentContextPurpose = Literal["decision_request", "plan_draft"]

ModelCallStatus = Literal["started", "succeeded", "failed", "invalid_output", "unknown"]
ModelCallSendState = Literal["not_sent", "may_have_been_sent", "response_received"]
AgentActionStatus = Literal["accepted", "applied", "rejected"]


class _BasePlanningAction(BaseModel):
    """Fields shared by the two advice-only model actions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[3] = 3
    reason: str = Field(min_length=1, max_length=512)
    expected_state: str = Field(min_length=1, max_length=64)
    input_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=16)

    @field_validator("input_refs")
    @classmethod
    def only_typed_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value or len(value) > 256 or "://" in value or ".." in value:
                raise ValueError("HARNESS_REFERENCE_INVALID")
        return values


class RequestDecisionAction(_BasePlanningAction):
    kind: Literal["request_decision"]
    decision: DecisionItem


class DraftPlanAction(_BasePlanningAction):
    kind: Literal["draft_plan"]


ActionEnvelope: TypeAlias = Annotated[
    RequestDecisionAction | DraftPlanAction,
    Field(discriminator="kind"),
]
ACTION_ENVELOPE_ADAPTER = TypeAdapter(ActionEnvelope)


def parse_action_envelope(value: object) -> ActionEnvelope:
    return ACTION_ENVELOPE_ADAPTER.validate_python(value)


def parse_action_envelope_json(value: str | bytes | bytearray) -> ActionEnvelope:
    return ACTION_ENVELOPE_ADAPTER.validate_json(value)


def action_envelope_json_schema() -> dict[str, object]:
    return ACTION_ENVELOPE_ADAPTER.json_schema()


class CanonicalModelRequest(BaseModel):
    """The complete, canonical input for one model-provider request.

    This object is hashed but never persisted.  It intentionally excludes
    credentials, local paths, timestamps, and random identifiers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    provider: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    endpoint_class: str = Field(min_length=1, max_length=64)
    prompt_template_version: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=4096)
    context_payload: dict[str, object]
    action_schema: dict[str, object]
    model_parameters: dict[str, object]
    model_profile_hash: str = Field(min_length=64, max_length=64)
    repair: bool = False


class ModelCallRecord(BaseModel):
    """Redacted ledger entry for one Harness provider invocation.

    ``request_hash`` and ``response_hash`` allow an attempt to be audited
    without retaining a prompt, raw response, credential, or image content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    call_id: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    phase: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    endpoint_class: str = Field(min_length=1, max_length=64)
    prompt_template_version: str = Field(min_length=1, max_length=128)
    context_hash: str = Field(min_length=1, max_length=128)
    skill_hashes: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    skill_error_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    request_hash: str = Field(min_length=1, max_length=128)
    action_schema_hash: str = Field(min_length=1, max_length=128)
    model_parameters_hash: str = Field(min_length=1, max_length=128)
    model_profile_hash: str = Field(min_length=64, max_length=64)
    request_bytes: int = Field(ge=1)
    request_builder_version: str = Field(min_length=1, max_length=128)
    response_schema_version: int = Field(ge=1, le=32)
    response_hash: str | None = Field(default=None, max_length=128)
    schema_valid: bool | None = None
    repair: bool = False
    started_at: datetime
    completed_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_request_id: str | None = Field(default=None, max_length=128)
    network_called: bool = False
    send_state: ModelCallSendState = "not_sent"
    status: ModelCallStatus = "started"
    error_code: str | None = Field(default=None, max_length=128)
    fallback_to: str | None = Field(default=None, max_length=128)


class AgentActionRecord(BaseModel):
    """Durable, replayable state of applying a validated Harness action.

    The payload is limited to the validated planning-action envelope. It
    carries no provider response or execution authority, but lets recovery
    finish an interrupted local action without asking the model again.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[3] = 3
    action_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(min_length=1, max_length=128)
    response_hash: str | None = Field(default=None, max_length=128)
    action_hash: str = Field(min_length=1, max_length=128)
    context_hash: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_snapshot_hash: str | None = Field(default=None, min_length=1, max_length=128)
    context_purpose: AgentContextPurpose | None = None
    kind: AgentHarnessActionKind
    expected_state: str = Field(min_length=1, max_length=64)
    action_payload: dict[str, Any] = Field(default_factory=dict)
    status: AgentActionStatus
    decision_batch_id: str | None = Field(default=None, min_length=1, max_length=128)
    decision_batch_hash: str | None = Field(default=None, min_length=1, max_length=128)
    reviewed_plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    reviewed_plan_hash: str | None = Field(default=None, min_length=1, max_length=128)
    error_code: str | None = Field(default=None, max_length=128)
    created_at: datetime
    completed_at: datetime | None = None


class AgentHarnessAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[5] = 5
    attempt_id: str
    lifecycle_id: str
    project_id: str
    status: AgentHarnessStatus = "READY"
    mode: Literal["single_agent"] = "single_agent"
    provider_ref: str
    context_hash: str | None = None
    next_step_no: int = Field(default=1, ge=1)
    model_calls_used: int = Field(default=0, ge=0)
    action_proposals_used: int = Field(default=0, ge=0)
    steps_used: int = Field(default=0, ge=0)
    repairs_used: int = Field(default=0, ge=0)
    input_tokens_used: int | None = Field(default=None, ge=0)
    output_tokens_used: int | None = Field(default=None, ge=0)
    cached_input_tokens_used: int | None = Field(default=None, ge=0)
    model_call_phase_allocations: dict[str, int] = Field(default_factory=dict)
    model_call_phase_usage: dict[str, int] = Field(default_factory=dict)
    active_seconds_used: float = Field(default=0, ge=0, le=300)
    active_interval_started_at: datetime | None = None
    active_interval_deadline_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    lease_takeovers: int = Field(default=0, ge=0)
    last_wake_reason: str | None = Field(default=None, max_length=128)
    last_wake_fingerprint: str | None = Field(default=None, max_length=128)
    last_progress_at: datetime | None = None
    yield_count: int = Field(default=0, ge=0)
    fallback_from: str | None = Field(default=None, max_length=256)
    fallback_to: str | None = Field(default=None, max_length=256)
    fallback_reason: str | None = Field(default=None, max_length=128)
    terminal_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentHarnessStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[5] = 5
    step_id: str
    attempt_id: str
    project_id: str
    step_no: int = Field(ge=1)
    idempotency_key: str
    kind: AgentHarnessActionKind | None = None
    input_hash: str
    skill_refs: tuple[SkillContextRef, ...] = Field(default_factory=tuple, max_length=3)
    action_hash: str | None = Field(default=None, max_length=128)
    action_result_hash: str | None = Field(default=None, max_length=128)
    observation_ref: str | None = Field(default=None, max_length=256)
    evaluation_ref: str | None = Field(default=None, max_length=256)
    action_id: str | None = Field(default=None, max_length=128)
    action_result_code: str | None = Field(default=None, max_length=128)
    validation_result: Literal["accepted", "rejected", "error"]
    model_calls: tuple[ModelCallRecord, ...] = Field(default_factory=tuple, max_length=2)
    state_before: str
    state_after: str | None = None
    summary: str = Field(default="", max_length=1024)
    started_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None


class AgentHarnessContextSection(BaseModel):
    """One immutable, provenance-bound, redacted Context v3 partition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_refs: tuple[str, ...] = ()
    source_hash: str
    data: dict[str, Any] = Field(default_factory=dict)


class AgentHarnessContextSections(BaseModel):
    """Fixed-order typed section container for the model-facing context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: AgentHarnessContextSection
    policy: AgentHarnessContextSection
    project_evidence: AgentHarnessContextSection
    decision_state: AgentHarnessContextSection
    plan_state: AgentHarnessContextSection
    execution_state: AgentHarnessContextSection
    latest_observation: AgentHarnessContextSection
    last_action_result: AgentHarnessContextSection
    memory_context: AgentHarnessContextSection
    budget: AgentHarnessContextSection


def agent_planning_base_context_hash(
    context: AgentPlanningBaseContext | Mapping[str, object],
) -> str:
    """Hash Base v1 fields while excluding its self-hash and creation time."""
    if isinstance(context, BaseModel):
        payload = context.model_dump(mode="json", exclude={"base_context_hash", "created_at"})
    else:
        payload = {
            key: value
            for key, value in context.items()
            if key not in {"base_context_hash", "created_at"}
        }
    return canonical_hash(payload)


class AgentPlanningBaseContext(BaseModel):
    """Frozen ten-section parent context shared by eligibility and reviewers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    base_context_hash: Sha256
    planning_epoch: int = Field(ge=0)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    sections: AgentHarnessContextSections
    section_hashes: dict[AgentHarnessContextSectionName, Sha256]
    evidence_refs: tuple[dict[str, str], ...] = Field(default_factory=tuple, max_length=128)
    evidence_snapshot_hash: str | None = Field(default=None, max_length=128)
    projection_policy_version: str = Field(min_length=1, max_length=128)
    complete: bool
    incomplete_reason: str | None = Field(default=None, max_length=128)
    project_snapshot_hash: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    redaction_policy_version: str = Field(min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("section_hashes")
    @classmethod
    def require_all_base_sections(
        cls, value: dict[AgentHarnessContextSectionName, str]
    ) -> dict[AgentHarnessContextSectionName, str]:
        if set(value) != set(AgentHarnessContextSections.model_fields):
            raise ValueError("AGENT_PLANNING_BASE_SECTIONS_INVALID")
        return value

    @model_validator(mode="after")
    def validate_base_hash(self) -> AgentPlanningBaseContext:
        if self.complete and self.incomplete_reason is not None:
            raise ValueError("AGENT_PLANNING_BASE_COMPLETENESS_INVALID")
        if not self.complete and self.incomplete_reason is None:
            raise ValueError("AGENT_PLANNING_BASE_COMPLETENESS_INVALID")
        for name in AgentHarnessContextSections.model_fields:
            if self.section_hashes[name] != canonical_hash(getattr(self.sections, name)):
                raise ValueError("AGENT_PLANNING_BASE_SECTION_HASH_MISMATCH")
        if agent_planning_base_context_hash(self) != self.base_context_hash:
            raise ValueError("AGENT_PLANNING_BASE_HASH_MISMATCH")
        return self


def agent_harness_context_v4_hash(context: AgentHarnessContext | Mapping[str, object]) -> str:
    """Hash the final Context v4 request identity, excluding its self-hash/time."""
    if isinstance(context, BaseModel):
        payload = context.model_dump(mode="json", exclude={"context_hash", "created_at"})
        advisory = context.team_advisory
        payload["team_advisory"] = {"advisory_hash": advisory.advisory_hash} if advisory else None
    else:
        payload = {
            key: value
            for key, value in context.items()
            if key not in {"context_hash", "created_at"}
        }
        advisory = payload.get("team_advisory")
        if isinstance(advisory, BaseModel):
            payload["team_advisory"] = {"advisory_hash": advisory.advisory_hash}
        elif isinstance(advisory, Mapping):
            payload["team_advisory"] = {"advisory_hash": advisory.get("advisory_hash")}
    return canonical_hash(payload)


class AgentHarnessContext(BaseModel):
    """Final Harness context bound to Base v1 and an optional typed advisory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[4] = 4
    context_hash: Sha256
    base_context_hash: Sha256
    planning_epoch: int = Field(ge=0)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    purpose: AgentContextPurpose
    planning_mode: AgentPlanningMode = "single_agent"
    team_id: str | None = Field(default=None, min_length=1, max_length=128)
    team_epoch: int | None = Field(default=None, ge=1)
    team_advisory: AgentTeamAdvisory | None = None
    sections: AgentHarnessContextSections
    section_hashes: dict[AgentHarnessContextV4SectionName, Sha256]
    required_sections: tuple[AgentHarnessContextSectionName, ...]
    included_sections: tuple[AgentHarnessContextV4SectionName, ...]
    omitted_sections: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, str], ...] = ()
    evidence_snapshot_hash: str | None = None
    projection_policy_version: str = Field(min_length=1, max_length=128)
    complete: bool
    incomplete_reason: str | None = None
    memory_context_hash: str | None = None
    project_snapshot_hash: str = Field(min_length=1, max_length=128)
    policy_version: str = "agent-harness-policy-v3"
    redaction_policy_version: str = "agent-harness-redaction-v3"
    prompt_template_version: str = "agent-harness-prompt-v3"
    skill_refs: tuple[SkillContextRef, ...] = Field(default_factory=tuple, max_length=3)
    skill_error_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_v4_binding(self) -> AgentHarnessContext:
        if len(set(self.required_sections)) != len(self.required_sections):
            raise ValueError("AGENT_HARNESS_CONTEXT_SECTIONS_INVALID")
        if len(set(self.included_sections)) != len(self.included_sections):
            raise ValueError("AGENT_HARNESS_CONTEXT_SECTIONS_INVALID")
        if not set(self.required_sections).issubset(self.included_sections):
            raise ValueError("AGENT_HARNESS_CONTEXT_REQUIRED_SECTION_MISSING")
        if set(self.section_hashes) != set(self.included_sections):
            raise ValueError("AGENT_HARNESS_CONTEXT_SECTION_HASH_MISMATCH")
        for name in self.included_sections:
            if name == "team_advisory":
                continue
            if self.section_hashes[name] != canonical_hash(getattr(self.sections, name)):
                raise ValueError("AGENT_HARNESS_CONTEXT_SECTION_HASH_MISMATCH")
        if self.planning_mode == "single_agent":
            if any(
                (
                    self.team_id is not None,
                    self.team_epoch is not None,
                    self.team_advisory is not None,
                )
            ):
                raise ValueError("AGENT_HARNESS_CONTEXT_TEAM_BINDING_INVALID")
            if "team_advisory" in self.included_sections:
                raise ValueError("AGENT_HARNESS_CONTEXT_TEAM_BINDING_INVALID")
        else:
            advisory = self.team_advisory
            if advisory is None or advisory.decision != "accepted":
                raise ValueError("AGENT_HARNESS_CONTEXT_TEAM_ADVISORY_REQUIRED")
            if self.team_id != advisory.team_id or self.team_epoch != advisory.team_epoch:
                raise ValueError("AGENT_HARNESS_CONTEXT_TEAM_BINDING_INVALID")
            if (self.project_id, self.lifecycle_id, self.base_context_hash) != (
                advisory.project_id,
                advisory.lifecycle_id,
                advisory.base_context_hash,
            ):
                raise ValueError("AGENT_HARNESS_CONTEXT_TEAM_BINDING_INVALID")
            if "team_advisory" not in self.included_sections:
                raise ValueError("AGENT_HARNESS_CONTEXT_TEAM_ADVISORY_REQUIRED")
            if self.section_hashes.get("team_advisory") != advisory.advisory_hash:
                raise ValueError("AGENT_HARNESS_CONTEXT_TEAM_ADVISORY_HASH_MISMATCH")
        if self.complete and self.incomplete_reason is not None:
            raise ValueError("AGENT_HARNESS_CONTEXT_COMPLETENESS_INVALID")
        if not self.complete and self.incomplete_reason is None:
            raise ValueError("AGENT_HARNESS_CONTEXT_COMPLETENESS_INVALID")
        if agent_harness_context_v4_hash(self) != self.context_hash:
            raise ValueError("AGENT_HARNESS_CONTEXT_HASH_MISMATCH")
        return self

    def prompt_payload(self) -> dict[str, Any]:
        """Return a fixed-order model payload with the typed advisory as data."""
        return {
            "schema_version": self.schema_version,
            "purpose": self.purpose,
            "required_sections": list(self.required_sections),
            "included_sections": list(self.included_sections),
            "omitted_sections": list(self.omitted_sections),
            "evidence_refs": [dict(reference) for reference in self.evidence_refs],
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "projection_policy_version": self.projection_policy_version,
            "complete": self.complete,
            "incomplete_reason": self.incomplete_reason,
            "policy_version": self.policy_version,
            "redaction_policy_version": self.redaction_policy_version,
            "prompt_template_version": self.prompt_template_version,
            "skill_refs": [reference.model_dump(mode="json") for reference in self.skill_refs],
            "skill_error_codes": list(self.skill_error_codes),
            "sections": {
                name: getattr(self.sections, name).model_dump(mode="json")
                for name in self.included_sections
                if name != "team_advisory"
            },
            "team_advisory": self.team_advisory.model_dump(mode="json")
            if self.team_advisory
            else None,
        }


def agent_team_role_context_hash(
    context: AgentTeamRoleContext | Mapping[str, object],
) -> str:
    """Hash the actual role projection, excluding only self-hash and timestamp."""
    if isinstance(context, BaseModel):
        payload = context.model_dump(mode="json", exclude={"role_context_hash", "created_at"})
    else:
        payload = {
            key: value
            for key, value in context.items()
            if key not in {"role_context_hash", "created_at"}
        }
    return canonical_hash(payload)


class AgentTeamRoleContext(BaseModel):
    """Bound read-only projection delivered to one fixed reviewer worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    worker_id: str = Field(min_length=1, max_length=128)
    role_id: Literal["science_reviewer.v1", "safety_reviewer.v1", "completeness_reviewer.v1"]
    reviewer_kind: Literal["science", "safety", "completeness"]
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    base_context_hash: Sha256
    role_context_hash: Sha256
    purpose: Literal["science_prerequisites", "project_safety", "goal_coverage"]
    included_sections: tuple[AgentHarnessContextSectionName, ...] = Field(
        min_length=1, max_length=10
    )
    omitted_sections: tuple[AgentHarnessContextSectionName, ...] = Field(max_length=10)
    sections: dict[AgentHarnessContextSectionName, AgentHarnessContextSection]
    section_hashes: dict[AgentHarnessContextSectionName, Sha256]
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    model_profile_hash: Sha256
    prompt_template_version: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    redaction_policy_version: str = Field(min_length=1, max_length=128)
    consent_epoch: int = Field(ge=0)
    consent_hash: Sha256
    budget_slice: AgentTeamBudgetSlice
    deadline_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_role_projection(self) -> AgentTeamRoleContext:
        expected_role = {
            "science": "science_reviewer.v1",
            "safety": "safety_reviewer.v1",
            "completeness": "completeness_reviewer.v1",
        }[self.reviewer_kind]
        expected_purpose = {
            "science": "science_prerequisites",
            "safety": "project_safety",
            "completeness": "goal_coverage",
        }[self.reviewer_kind]
        if self.role_id != expected_role:
            raise ValueError("MULTI_AGENT_REVIEWER_KIND_MISMATCH")
        if self.purpose != expected_purpose:
            raise ValueError("TEAM_ROLE_CONTEXT_PURPOSE_INVALID")
        if len(set(self.included_sections)) != len(self.included_sections):
            raise ValueError("TEAM_ROLE_CONTEXT_SECTION_DUPLICATE")
        if len(set(self.omitted_sections)) != len(self.omitted_sections):
            raise ValueError("TEAM_ROLE_CONTEXT_SECTION_DUPLICATE")
        if set(self.included_sections) & set(self.omitted_sections):
            raise ValueError("TEAM_ROLE_CONTEXT_SECTION_SET_INVALID")
        if set(self.included_sections) | set(self.omitted_sections) != set(
            AgentHarnessContextSections.model_fields
        ):
            raise ValueError("TEAM_ROLE_CONTEXT_SECTION_SET_INVALID")
        if set(self.sections) != set(self.included_sections):
            raise ValueError("TEAM_ROLE_CONTEXT_SECTION_SET_INVALID")
        if set(self.section_hashes) != set(self.included_sections):
            raise ValueError("TEAM_ROLE_CONTEXT_SECTION_HASH_MISMATCH")
        if any(
            self.section_hashes[name] != canonical_hash(self.sections[name])
            for name in self.included_sections
        ):
            raise ValueError("TEAM_ROLE_CONTEXT_SECTION_HASH_MISMATCH")
        if agent_team_role_context_hash(self) != self.role_context_hash:
            raise ValueError("TEAM_ROLE_CONTEXT_HASH_MISMATCH")
        return self


class AgentHarnessContextV3Projection(BaseModel):
    """Internal pre-V4 projection used only while constructing durable snapshots."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[3] = 3
    context_hash: str
    lifecycle_id: str
    project_id: str
    purpose: AgentContextPurpose
    sections: AgentHarnessContextSections
    section_hashes: dict[AgentHarnessContextSectionName, str]
    required_sections: tuple[AgentHarnessContextSectionName, ...]
    included_sections: tuple[AgentHarnessContextSectionName, ...]
    omitted_sections: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, str], ...] = ()
    evidence_snapshot_hash: str | None = None
    projection_policy_version: str
    complete: bool
    incomplete_reason: str | None = None
    memory_context_hash: str | None = None
    project_snapshot_hash: str
    policy_version: str = "agent-harness-policy-v2"
    redaction_policy_version: str = "agent-harness-redaction-v2"
    prompt_template_version: str = "agent-harness-prompt-v2"
    skill_refs: tuple[SkillContextRef, ...] = Field(default_factory=tuple, max_length=3)
    skill_error_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def prompt_payload(self) -> dict[str, Any]:
        """Return the only fixed-order payload supplied to an action provider."""
        return {
            "schema_version": self.schema_version,
            "purpose": self.purpose,
            "required_sections": list(self.required_sections),
            "included_sections": list(self.included_sections),
            "omitted_sections": list(self.omitted_sections),
            "evidence_refs": [dict(reference) for reference in self.evidence_refs],
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "projection_policy_version": self.projection_policy_version,
            "complete": self.complete,
            "incomplete_reason": self.incomplete_reason,
            "policy_version": self.policy_version,
            "redaction_policy_version": self.redaction_policy_version,
            "prompt_template_version": self.prompt_template_version,
            "skill_refs": [reference.model_dump(mode="json") for reference in self.skill_refs],
            "skill_error_codes": list(self.skill_error_codes),
            "sections": {
                name: getattr(self.sections, name).model_dump(mode="json")
                for name in self.included_sections
            },
        }


class AgentHarnessSummary(BaseModel):
    """Read-only, redacted projection exposed in Agent Task responses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AgentHarnessStatus
    model_calls_used: int = Field(ge=0)
    model_calls_limit: int = Field(ge=1)
    action_proposals_used: int = Field(ge=0)
    action_proposals_limit: int = Field(ge=1)
    steps_used: int = Field(ge=0)
    steps_limit: int = Field(ge=1)
    repairs_used: int = Field(ge=0)
    repairs_limit: int = Field(ge=0)
    input_tokens_used: int | None = Field(default=None, ge=0)
    input_tokens_limit: int | None = Field(default=None, ge=1)
    output_tokens_used: int | None = Field(default=None, ge=0)
    output_tokens_limit: int | None = Field(default=None, ge=1)
    actual_provider: str | None = None
    next_step: str | None = None
    terminal_reason: str | None = None
    latest_step_id: str | None = None
    latest_step_summary: str | None = None
    last_wake_reason: str | None = None
    yield_count: int = Field(default=0, ge=0)
    fallback_from: str | None = None
    fallback_to: str | None = None
    fallback_reason: str | None = None
