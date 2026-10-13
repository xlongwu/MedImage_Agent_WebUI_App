"""Typed, immutable records for the optional planning-review team."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import to_jsonable_python

from src.backend.app.schemas.agent_eval import AgentReviewerKind, AgentReviewFinding

Sha256 = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")]
AgentPlanningMode = Literal["single_agent", "multi_agent"]
AgentTeamRoleId = Literal[
    "science_reviewer.v1",
    "safety_reviewer.v1",
    "completeness_reviewer.v1",
]
AgentTeamStatus = Literal[
    "READY",
    "RUNNING",
    "AGGREGATING",
    "COMPLETED",
    "NEEDS_ATTENTION",
    "CANCELED",
    "FAILED",
]
AgentTeamWorkItemStatus = Literal[
    "PENDING",
    "CLAIMED",
    "MODEL_STARTED",
    "VALIDATING",
    "RETRY_READY",
    "COMPLETED",
    "FAILED",
    "INDETERMINATE",
    "CANCELED",
]
AgentTeamModelCallStatus = Literal["started", "completed", "failed", "canceled", "indeterminate"]
AgentTeamWakeStatus = Literal["PENDING", "CLAIMED", "CONSUMED", "RETRY", "CANCELED"]
AgentTeamEventType = Literal[
    "TEAM_REQUESTED",
    "TEAM_GATE_ACCEPTED",
    "TEAM_GATE_REJECTED",
    "TEAM_CONTEXT_FROZEN",
    "WORK_ITEM_CLAIMED",
    "MODEL_CALL_STARTED",
    "MODEL_CALL_COMPLETED",
    "MODEL_CALL_FAILED",
    "MODEL_CALL_INDETERMINATE",
    "FINDING_ACCEPTED",
    "FINDING_REJECTED",
    "TEAM_CONFLICT_DETECTED",
    "TEAM_ADVISORY_FROZEN",
    "TEAM_NEEDS_ATTENTION",
    "TEAM_FAILED",
    "TEAM_CANCELED",
    "TEAM_BOUND_TO_PLANNING_REQUEST",
    "TEAM_BINDING_INVALIDATED",
]
AgentTeamErrorCode = Literal[
    "MULTI_AGENT_REVIEW_CONTEXT_REDACTION_VIOLATION",
    "MULTI_AGENT_REVIEW_FINDING_CODE_UNKNOWN",
    "MULTI_AGENT_REVIEW_INPUT_REF_INVALID",
    "MULTI_AGENT_REVIEW_FORBIDDEN_SUGGESTION",
    "MULTI_AGENT_REVIEW_SAFETY_REQUIRED",
    "MULTI_AGENT_REVIEW_OUTPUT_INVALID",
    "MULTI_AGENT_REVIEWER_KIND_MISMATCH",
    "MULTI_AGENT_DISABLED",
    "MULTI_AGENT_CONSENT_INVALIDATED",
    "MULTI_AGENT_BUDGET_EXCEEDED",
    "AGENT_MODEL_CONFIG_INCOMPLETE",
    "AGENT_MODEL_CONFIG_INVALID",
    "AGENT_MODEL_PROVIDER_FAILED",
    "AGENT_MODEL_PROVIDER_TIMEOUT",
    "AGENT_TEAM_PROVIDER_UNAVAILABLE",
    "AGENT_TEAM_CONTEXT_BINDING_INVALID",
    "AGENT_TEAM_PLANNING_STATE_INVALID",
    "TEAM_ROLE_REGISTRY_INVALID",
    "TEAM_ROLE_CONTEXT_DEADLINE_EXPIRED",
    "TEAM_SCHEDULER_FAILED",
    "TEAM_CONFLICT_DETECTED",
    "TEAM_BINDING_INVALIDATED",
]

_ROLE_ID_BY_KIND: dict[str, str] = {
    "science": "science_reviewer.v1",
    "safety": "safety_reviewer.v1",
    "completeness": "completeness_reviewer.v1",
}
_ALL_REVIEWER_KINDS = frozenset(_ROLE_ID_BY_KIND)


def canonical_hash(value: BaseModel | Mapping[str, object]) -> str:
    """Hash the actual JSON payload using the shared canonical serialization."""
    payload: object
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:  # pragma: no cover - the signature and runtime guard are intentional
        raise TypeError("canonical_hash expects a Pydantic model or mapping")
    serialized = json.dumps(
        to_jsonable_python(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _content_hash(value: BaseModel | Mapping[str, object], *, excluded: set[str]) -> str:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", exclude=excluded)
    else:
        payload = {key: item for key, item in value.items() if key not in excluded}
    return canonical_hash(payload)


def agent_team_finding_hash(finding: AgentTeamFinding | Mapping[str, object]) -> str:
    """Return a finding identity bound to its team, role, response and content."""
    return _content_hash(finding, excluded={"finding_hash", "created_at"})


def agent_team_context_snapshot_hash(
    snapshot: AgentTeamContextSnapshot | Mapping[str, object],
) -> str:
    """Return the content identity of a frozen Team context snapshot."""
    return _content_hash(snapshot, excluded={"snapshot_hash", "created_at"})


def agent_team_advisory_hash(advisory: AgentTeamAdvisory | Mapping[str, object]) -> str:
    """Return the deterministic identity of one frozen aggregation result."""
    return _content_hash(advisory, excluded={"advisory_hash", "created_at"})


class AgentTeamBudget(BaseModel):
    """Frozen Team-level limits reserved by the parent planning task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_model_calls: int = Field(ge=3, le=6)
    max_repairs: int = Field(ge=0, le=3)
    reserved_input_tokens: int = Field(ge=1)
    deadline_at: datetime

    @model_validator(mode="after")
    def bind_calls_to_repair_cap(self) -> AgentTeamBudget:
        if self.max_model_calls > 3 + self.max_repairs:
            raise ValueError("TEAM_BUDGET_REPAIR_CALLS_INVALID")
        return self


class AgentTeamBudgetSlice(BaseModel):
    """The fixed call and token allowance assigned to one reviewer role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_model_calls: int = Field(default=2, ge=1, le=2)
    reserved_input_tokens: int = Field(ge=1)
    deadline_at: datetime


class AgentTeamRecord(BaseModel):
    """Durable child state; it never replaces the parent lifecycle state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    mode: Literal["multi_agent"] = "multi_agent"
    status: AgentTeamStatus = "READY"
    snapshot_hash: Sha256
    role_registry_hash: Sha256
    context_projector_hash: Sha256
    aggregation_policy_hash: Sha256
    model_profile_hash: Sha256
    global_policy_hash: Sha256
    consent_epoch: int = Field(ge=0)
    consent_hash: Sha256
    budget: AgentTeamBudget
    lease_owner: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: datetime | None = None
    fencing_token: int = Field(default=0, ge=0)
    advisory_id: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_lease_and_terminal_binding(self) -> AgentTeamRecord:
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("TEAM_LEASE_BINDING_INVALID")
        if self.lease_owner is not None and self.fencing_token < 1:
            raise ValueError("TEAM_FENCING_TOKEN_INVALID")
        if self.status == "COMPLETED" and self.advisory_id is None:
            raise ValueError("TEAM_COMPLETED_WITHOUT_ADVISORY")
        return self


class AgentTeamContextSnapshot(BaseModel):
    """Frozen link from one Team epoch to its base and three role projections."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_hash: Sha256
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    base_context_hash: Sha256
    role_context_hashes: dict[AgentReviewerKind, Sha256]
    consent_epoch: int = Field(ge=0)
    consent_hash: Sha256
    role_registry_hash: Sha256
    context_projector_hash: Sha256
    policy_version: str = Field(min_length=1, max_length=128)
    redaction_policy_version: str = Field(min_length=1, max_length=128)
    prompt_template_version: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_all_fixed_roles(self) -> AgentTeamContextSnapshot:
        if set(self.role_context_hashes) != _ALL_REVIEWER_KINDS:
            raise ValueError("TEAM_ROLE_CONTEXT_SET_INVALID")
        if agent_team_context_snapshot_hash(self) != self.snapshot_hash:
            raise ValueError("TEAM_CONTEXT_SNAPSHOT_HASH_MISMATCH")
        return self


class AgentTeamWorkItem(BaseModel):
    """One bounded reviewer invocation and its lease-fenced progress."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    work_item_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    reviewer_kind: AgentReviewerKind
    role_id: AgentTeamRoleId
    dependency_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    attempt_no: int = Field(default=1, ge=1, le=2)
    status: AgentTeamWorkItemStatus = "PENDING"
    request_hash: Sha256 | None = None
    model_call_id: str | None = Field(default=None, min_length=1, max_length=128)
    available_at: datetime
    deadline_at: datetime
    lease_owner: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: datetime | None = None
    fencing_token: int = Field(default=0, ge=0)
    error_code: AgentTeamErrorCode | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_role_and_lease(self) -> AgentTeamWorkItem:
        if self.role_id != _ROLE_ID_BY_KIND[self.reviewer_kind]:
            raise ValueError("MULTI_AGENT_REVIEWER_KIND_MISMATCH")
        if len(set(self.dependency_ids)) != len(self.dependency_ids):
            raise ValueError("TEAM_WORK_ITEM_DEPENDENCY_DUPLICATE")
        if self.work_item_id in self.dependency_ids:
            raise ValueError("TEAM_WORK_ITEM_SELF_DEPENDENCY")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("TEAM_LEASE_BINDING_INVALID")
        if self.lease_owner is not None and self.fencing_token < 1:
            raise ValueError("TEAM_FENCING_TOKEN_INVALID")
        if self.status in {
            "MODEL_STARTED",
            "VALIDATING",
            "RETRY_READY",
            "COMPLETED",
            "INDETERMINATE",
        }:
            if self.model_call_id is None:
                raise ValueError("TEAM_WORK_ITEM_MODEL_CALL_REQUIRED")
        if self.status in {"FAILED", "INDETERMINATE"} and self.error_code is None:
            raise ValueError("TEAM_WORK_ITEM_ERROR_CODE_REQUIRED")
        return self


class AgentTeamModelCallRecord(BaseModel):
    """Redacted durable ledger for a single structured-provider request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    model_call_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    work_item_id: str = Field(min_length=1, max_length=128)
    reviewer_kind: AgentReviewerKind
    role_id: AgentTeamRoleId
    role_registry_hash: Sha256
    role_context_hash: Sha256
    model_profile_hash: Sha256
    request_hash: Sha256
    request_bytes: int = Field(ge=1)
    provider_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    endpoint_class: str = Field(min_length=1, max_length=64)
    prompt_template_version: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=128)
    consent_epoch: int = Field(ge=0)
    consent_hash: Sha256
    attempt_no: int = Field(default=1, ge=1, le=2)
    network_called: bool = False
    provider_request_id: str | None = Field(default=None, max_length=128)
    status: AgentTeamModelCallStatus = "started"
    response_hash: Sha256 | None = None
    schema_valid: bool | None = None
    repair_of_model_call_id: str | None = Field(default=None, min_length=1, max_length=128)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: AgentTeamErrorCode | None = None
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_call_binding(self) -> AgentTeamModelCallRecord:
        if self.role_id != _ROLE_ID_BY_KIND[self.reviewer_kind]:
            raise ValueError("MULTI_AGENT_REVIEWER_KIND_MISMATCH")
        if self.status == "started" and self.completed_at is not None:
            raise ValueError("TEAM_MODEL_CALL_STATUS_INVALID")
        if (
            self.status in {"completed", "failed", "canceled", "indeterminate"}
            and self.completed_at is None
        ):
            raise ValueError("TEAM_MODEL_CALL_COMPLETION_TIME_REQUIRED")
        if self.status == "completed" and (not self.network_called or self.response_hash is None):
            raise ValueError("TEAM_MODEL_CALL_RESPONSE_BINDING_INVALID")
        if self.status == "indeterminate" and not self.network_called:
            raise ValueError("TEAM_MODEL_CALL_INDETERMINATE_BEFORE_NETWORK")
        if (self.attempt_no == 1) != (self.repair_of_model_call_id is None):
            raise ValueError("TEAM_REPAIR_CALL_BINDING_INVALID")
        if self.repair_of_model_call_id == self.model_call_id:
            raise ValueError("TEAM_REPAIR_CALL_BINDING_INVALID")
        if self.status in {"failed", "indeterminate"} and self.error_code is None:
            raise ValueError("TEAM_MODEL_CALL_ERROR_CODE_REQUIRED")
        return self


class AgentTeamFinding(AgentReviewFinding):
    """A reviewer finding bound to its actual Team, call and role context."""

    schema_version: Literal[1] = 1
    finding_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    work_item_id: str = Field(min_length=1, max_length=128)
    role_id: AgentTeamRoleId
    role_context_hash: Sha256
    model_call_id: str = Field(min_length=1, max_length=128)
    model_response_hash: Sha256
    finding_hash: Sha256
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_finding_identity(self) -> AgentTeamFinding:
        if self.role_id != _ROLE_ID_BY_KIND[self.reviewer_kind]:
            raise ValueError("MULTI_AGENT_REVIEWER_KIND_MISMATCH")
        if agent_team_finding_hash(self) != self.finding_hash:
            raise ValueError("TEAM_FINDING_HASH_MISMATCH")
        return self


class AgentTeamAdvisory(BaseModel):
    """Deterministic, hash-bound summary passed to the single planner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    advisory_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    base_context_hash: Sha256
    context_snapshot_hash: Sha256
    context_projector_hash: Sha256
    role_registry_hash: Sha256
    aggregation_policy_hash: Sha256
    model_profile_hash: Sha256
    consent_epoch: int = Field(ge=0)
    consent_hash: Sha256
    policy_version: str = Field(min_length=1, max_length=128)
    redaction_policy_version: str = Field(min_length=1, max_length=128)
    accepted_finding_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    rejected_finding_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    blocking_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    conflict_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    coverage_by_role: dict[AgentReviewerKind, int]
    safety_reviewer_present: bool
    decision: Literal["accepted", "rejected"]
    advisory_hash: Sha256
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_advisory(self) -> AgentTeamAdvisory:
        if len(set(self.accepted_finding_ids)) != len(self.accepted_finding_ids):
            raise ValueError("TEAM_ADVISORY_FINDING_DUPLICATE")
        if len(set(self.rejected_finding_ids)) != len(self.rejected_finding_ids):
            raise ValueError("TEAM_ADVISORY_FINDING_DUPLICATE")
        if set(self.accepted_finding_ids) & set(self.rejected_finding_ids):
            raise ValueError("TEAM_ADVISORY_FINDING_CONFLICT")
        if set(self.coverage_by_role) != _ALL_REVIEWER_KINDS:
            raise ValueError("TEAM_ADVISORY_ROLE_COVERAGE_INVALID")
        if any(value < 0 or value > 24 for value in self.coverage_by_role.values()):
            raise ValueError("TEAM_ADVISORY_ROLE_COVERAGE_INVALID")
        if self.decision == "accepted" and not self.safety_reviewer_present:
            raise ValueError("MULTI_AGENT_REVIEW_SAFETY_REQUIRED")
        if self.decision == "accepted" and (self.blocking_codes or self.conflict_codes):
            raise ValueError("TEAM_ADVISORY_BLOCKING_FINDING_ACCEPTED")
        if agent_team_advisory_hash(self) != self.advisory_hash:
            raise ValueError("TEAM_ADVISORY_HASH_MISMATCH")
        return self


class AgentTeamEvent(BaseModel):
    """Append-only event envelope containing only a payload hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=256)
    source: Literal["user", "coordinator", "scheduler", "worker", "system"]
    event_type: AgentTeamEventType
    payload_hash: Sha256
    occurred_at: datetime


class AgentTeamWakeRecord(BaseModel):
    """Durable, idempotent wake for the independent Team scheduler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    wake_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=128)
    team_epoch: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    step_key: str = Field(min_length=1, max_length=256)
    reason: Literal["work_item", "aggregate", "planning_resume"]
    status: AgentTeamWakeStatus = "PENDING"
    available_at: datetime
    lease_owner: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: datetime | None = None
    fencing_token: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=0, ge=0)
    last_error_code: AgentTeamErrorCode | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_wake_lease(self) -> AgentTeamWakeRecord:
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("TEAM_LEASE_BINDING_INVALID")
        if self.lease_owner is not None and self.fencing_token < 1:
            raise ValueError("TEAM_FENCING_TOKEN_INVALID")
        if self.status == "RETRY" and self.last_error_code is None:
            raise ValueError("TEAM_WAKE_ERROR_CODE_REQUIRED")
        return self


_TEAM_TRANSITIONS: dict[str, frozenset[str]] = {
    "READY": frozenset({"RUNNING", "NEEDS_ATTENTION", "CANCELED", "FAILED"}),
    "RUNNING": frozenset({"AGGREGATING", "NEEDS_ATTENTION", "CANCELED", "FAILED"}),
    "AGGREGATING": frozenset({"COMPLETED", "NEEDS_ATTENTION", "CANCELED", "FAILED"}),
    "COMPLETED": frozenset(),
    "NEEDS_ATTENTION": frozenset(),
    "CANCELED": frozenset(),
    "FAILED": frozenset(),
}
_WORK_ITEM_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"CLAIMED", "CANCELED"}),
    "CLAIMED": frozenset({"MODEL_STARTED", "CANCELED", "FAILED"}),
    "MODEL_STARTED": frozenset({"VALIDATING", "INDETERMINATE", "CANCELED", "FAILED"}),
    "VALIDATING": frozenset({"RETRY_READY", "COMPLETED", "FAILED", "CANCELED"}),
    "RETRY_READY": frozenset({"CLAIMED", "CANCELED", "FAILED"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "INDETERMINATE": frozenset(),
    "CANCELED": frozenset(),
}
_WAKE_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"CLAIMED", "CANCELED"}),
    "CLAIMED": frozenset({"CONSUMED", "RETRY", "CANCELED"}),
    "RETRY": frozenset({"CLAIMED", "CANCELED"}),
    "CONSUMED": frozenset(),
    "CANCELED": frozenset(),
}


def team_transition_allowed(current: AgentTeamStatus, target: AgentTeamStatus) -> bool:
    return target in _TEAM_TRANSITIONS[current]


def work_item_transition_allowed(
    current: AgentTeamWorkItemStatus, target: AgentTeamWorkItemStatus
) -> bool:
    return target in _WORK_ITEM_TRANSITIONS[current]


def wake_transition_allowed(current: AgentTeamWakeStatus, target: AgentTeamWakeStatus) -> bool:
    return target in _WAKE_TRANSITIONS[current]
