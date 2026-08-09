"""Persisted, bounded control-plane records for the optional Agent Harness.

The models in this module intentionally describe *advice* and its audit trail.
They carry no execution ticket, approval, runner, filesystem, or shell fields.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.backend.app.agent_skills.schemas import SkillContextRef

AgentHarnessStatus = Literal[
    "READY",
    "RUNNING",
    "WAITING_FOR_USER",
    "FINISHED",
    "STOPPED",
    "FAILED",
]
AgentHarnessActionKind = Literal[
    "read_evidence",
    "request_decision",
    "draft_plan",
    "explain_result",
    "propose_recovery",
    "finish",
]
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

ModelCallStatus = Literal["started", "succeeded", "failed", "invalid_output", "unknown"]


class ModelCallRecord(BaseModel):
    """Redacted ledger entry for one Harness provider invocation.

    ``request_hash`` and ``response_hash`` allow an attempt to be audited
    without retaining a prompt, raw response, credential, or image content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
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
    status: ModelCallStatus = "started"
    error_code: str | None = Field(default=None, max_length=128)
    fallback_to: str | None = Field(default=None, max_length=128)


class ActionEnvelope(BaseModel):
    """The only model-to-Harness protocol.

    The strict schema is a safety boundary: a model can suggest one of six
    bounded planning actions, never a command or an execution capability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    kind: AgentHarnessActionKind
    reason: str = Field(min_length=1, max_length=512)
    input_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=32)
    expected_state: str = Field(min_length=1, max_length=64)

    @field_validator("input_refs")
    @classmethod
    def only_typed_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value or len(value) > 256 or "://" in value or ".." in value:
                raise ValueError("HARNESS_REFERENCE_INVALID")
        return values


class AgentHarnessAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[3] = 3
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
    recovery_attempts_used: int = Field(default=0, ge=0)
    input_tokens_used: int | None = Field(default=None, ge=0)
    output_tokens_used: int | None = Field(default=None, ge=0)
    cached_input_tokens_used: int | None = Field(default=None, ge=0)
    model_call_phase_allocations: dict[str, int] = Field(default_factory=dict)
    model_call_phase_usage: dict[str, int] = Field(default_factory=dict)
    deadline_at: datetime
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

    schema_version: Literal[3] = 3
    step_id: str
    attempt_id: str
    project_id: str
    step_no: int = Field(ge=1)
    idempotency_key: str
    kind: AgentHarnessActionKind | None = None
    input_hash: str
    skill_refs: tuple[SkillContextRef, ...] = Field(default_factory=tuple, max_length=3)
    output_hash: str | None = None
    observation_ref: str | None = Field(default=None, max_length=256)
    evaluation_ref: str | None = Field(default=None, max_length=256)
    recovery_proposal_ref: str | None = Field(default=None, max_length=256)
    result_explanation_hash: str | None = Field(default=None, max_length=128)
    generated_text: str | None = Field(default=None, max_length=2048)
    action_result_code: str | None = Field(default=None, max_length=128)
    requested_capability: str | None = None
    validation_result: Literal["accepted", "rejected", "error"]
    model_calls: tuple[ModelCallRecord, ...] = Field(default_factory=tuple, max_length=2)
    state_before: str
    state_after: str | None = None
    summary: str = Field(default="", max_length=1024)
    started_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None


class AgentHarnessContextSection(BaseModel):
    """One immutable, provenance-bound, redacted Context v2 partition."""

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


class AgentHarnessContext(BaseModel):
    """Immutable, redacted, fixed-section Context v2 snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    context_hash: str
    lifecycle_id: str
    project_id: str
    sections: AgentHarnessContextSections
    section_hashes: dict[AgentHarnessContextSectionName, str]
    memory_context_hash: str | None = None
    project_snapshot_hash: str
    policy_version: str = "agent-harness-policy-v2"
    redaction_policy_version: str = "agent-harness-redaction-v2"
    prompt_template_version: str = "agent-harness-prompt-v2"
    skill_refs: tuple[SkillContextRef, ...] = Field(default_factory=tuple, max_length=3)
    skill_error_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    omitted_fields: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def prompt_payload(self) -> dict[str, Any]:
        """Return the only fixed-order payload supplied to an action provider."""
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "redaction_policy_version": self.redaction_policy_version,
            "prompt_template_version": self.prompt_template_version,
            "skill_refs": [reference.model_dump(mode="json") for reference in self.skill_refs],
            "skill_error_codes": list(self.skill_error_codes),
            "sections": self.sections.model_dump(mode="json"),
            "omitted_fields": list(self.omitted_fields),
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
    recovery_attempts_used: int = Field(ge=0)
    recovery_attempts_limit: int = Field(ge=1)
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
