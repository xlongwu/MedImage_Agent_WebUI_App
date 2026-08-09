"""Typed contracts for the project-scoped long-term Memory Domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryKind = Literal[
    "user_preference",
    "project_decision",
    "environment_fact",
    "workflow_lesson",
    "error_lesson",
    "presentation_preference",
]
MemoryImpactClass = Literal["presentation", "workflow", "scientific", "safety"]
MemorySensitivity = Literal["public", "project_internal", "restricted", "rejected"]
MemoryTrustClass = Literal[
    "explicit_user",
    "authoritative_structured",
    "external_untrusted",
]
MemoryCandidateStatus = Literal[
    "proposed", "accepted", "rejected", "expired", "suppressed"
]
MemoryItemStatus = Literal["active", "expired", "forgotten", "conflicted", "merged"]


class MemoryConsentStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    project_id: str
    status: Literal["disabled", "healthy", "partial", "failure"]
    available: bool
    generation_available: bool
    use_available: bool
    generate_enabled: bool = False
    use_enabled: bool = False
    consent_epoch: int = 0
    outbox_cutoff_sequence: int = 0
    updated_at: datetime | None = None
    degraded_reason: str | None = None
    retrieval_policy_version: str
    store_healthy: bool
    outbox_max_sequence: int = 0
    processed_outbox_sequence: int = 0
    outbox_lag: int = 0
    retry_jobs: int = 0
    dead_letter_jobs: int = 0
    active_leases: int = 0
    expired_leases: int = 0
    pending_forget_records: int = 0
    last_forget_wal_truncate_at: datetime | None = None


class MemorySource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str
    source_id: str
    source_hash: str
    source_ref: str
    source_trust_class: MemoryTrustClass
    source_sequence: int | None = None
    stale: bool = False


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    project_id: str
    scope_type: Literal["project"] = "project"
    kind: MemoryKind
    canonical_key: str
    content: dict[str, Any]
    content_text: str
    impact_class: MemoryImpactClass
    source: MemorySource
    consent_epoch: int
    extractor: str
    policy_version: str
    model: str | None = None
    prompt_version: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sensitivity: MemorySensitivity = "project_internal"
    status: MemoryCandidateStatus = "proposed"
    requires_review: bool = True
    rejection_code: str | None = None
    dedupe_hash: str
    candidate_hash: str
    candidate_version: int = Field(default=1, ge=1)
    created_at: datetime
    expires_at: datetime | None = None


class MemoryRevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_id: str
    memory_id: str
    revision_number: int = Field(ge=1)
    generation: int = Field(default=0, ge=0)
    content: dict[str, Any]
    content_text: str
    content_hash: str
    impact_class: MemoryImpactClass
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sensitivity: MemorySensitivity = "project_internal"
    confirmation_status: str = "confirmed"
    algorithm_id: str | None = None
    algorithm_version: str | None = None
    config_fingerprint: str | None = None
    applicability: dict[str, Any] = Field(default_factory=dict)
    confirmation_event_id: str | None = None
    change_reason: str
    created_at: datetime


class MemoryItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    project_id: str
    scope_type: Literal["project"] = "project"
    kind: MemoryKind
    canonical_key: str
    current_revision_id: str
    item_version: int = Field(default=1, ge=1)
    generation: int = Field(default=0, ge=0)
    status: MemoryItemStatus = "active"
    pinned: bool = False
    superseded_by_memory_id: str | None = None
    valid_from: datetime
    valid_until: datetime | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    revision: MemoryRevision
    sources: tuple[MemorySource, ...] = ()


class MemoryItemDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item: MemoryItem
    revisions: tuple[MemoryRevision, ...]
    events: tuple[MemoryEvent, ...] = ()


class MemoryEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    project_id: str
    memory_id: str | None = None
    candidate_id: str | None = None
    command_id: str | None = None
    principal: str
    event_type: str
    before_hash: str | None = None
    after_hash: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class MemoryDecisionSuggestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    revision_hash: str
    decision_kind: str
    typed_value: dict[str, Any]
    algorithm_id: str
    algorithm_version: str
    config_fingerprint: str
    applicability: dict[str, Any]
    confirmation_event_id: str
    source_refs: tuple[str, ...]
    advisory_only: Literal[True] = True
    confirmation_policy: Literal["confirm_each_agent_task"] = "confirm_each_agent_task"


class MemoryEvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: MemoryKind
    memory_id: str
    revision_hash: str
    source_ref: str
    provenance_warning: str | None = None


class MemoryContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["memory-context-v1"] = "memory-context-v1"
    retrieval_policy_version: Literal["memory-retrieval-v1"] = "memory-retrieval-v1"
    project_id: str
    planner_constraints: dict[str, Any] = Field(default_factory=dict)
    decision_suggestions: tuple[MemoryDecisionSuggestion, ...] = ()
    evidence_refs: tuple[MemoryEvidenceRef, ...] = ()
    omitted_count: int = Field(default=0, ge=0)
    used_bytes: int = Field(default=0, ge=0)
    status: Literal["disabled", "enabled", "partial"]
    warning_codes: tuple[str, ...] = ()
    context_hash: str


class MemoryPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[MemoryItem, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = None


class MemoryCandidatePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[MemoryCandidate, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = None


class MemoryEventPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[MemoryEvent, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = None


class SetMemoryConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=8, max_length=200)
    generate_enabled: bool
    use_enabled: bool


class RememberMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=8, max_length=200)
    kind: MemoryKind
    key: str = Field(min_length=1, max_length=200)
    value: dict[str, Any]
    summary: str = Field(min_length=1, max_length=1000)
    impact_class: MemoryImpactClass


class ReviewMemoryCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=8, max_length=200)
    expected_candidate_version: int = Field(ge=1)
    candidate_hash: str
    edited_value: dict[str, Any] | None = None
    edited_summary: str | None = Field(default=None, max_length=1000)
    reason: str | None = Field(default=None, max_length=500)


class MutateMemoryItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=8, max_length=200)
    expected_item_version: int = Field(ge=1)
    pinned: bool | None = None
    expected_revision_hash: str | None = None
    value: dict[str, Any] | None = None
    summary: str | None = Field(default=None, max_length=1000)
    reason: str | None = Field(default=None, max_length=500)


class MemoryContextPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    lifecycle_id: str | None = None
