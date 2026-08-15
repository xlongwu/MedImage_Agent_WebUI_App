"""Read-only, redacted Agent Harness trace and replay contracts.

Trace records are projections over canonical lifecycle, Harness, planning, and
runtime records.  They deliberately do not introduce a second persisted source
of truth and never carry prompts, raw model responses, filesystem paths, or
research data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.backend.app.schemas.agent_harness import AgentActionRecord, ModelCallRecord

TraceIntegrityStatus = Literal["complete", "incomplete", "conflict"]
TraceReferenceStatus = Literal["present", "missing", "conflict"]
ReplayViolationCode = Literal[
    "TRACE_INTEGRITY_HASH_MISMATCH",
    "TRACE_ENTRY_ORDER_INVALID",
    "TRACE_STEP_IDEMPOTENCY_DUPLICATE",
    "TRACE_STEP_STATE_MISMATCH",
    "TRACE_CAPABILITY_DENIED",
    "TRACE_LIFECYCLE_EVENT_CHAIN_INVALID",
    "TRACE_FINAL_STATE_MISMATCH",
    "TRACE_BUDGET_MISMATCH",
    "TRACE_REFERENCE_MISSING",
    "TRACE_REFERENCE_CONFLICT",
]


class AgentTraceReference(BaseModel):
    """A safe pointer to a canonical record, without copying its payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref_type: str = Field(min_length=1, max_length=64)
    ref_id: str = Field(min_length=1, max_length=256)
    content_hash: str | None = Field(default=None, max_length=128)
    status: TraceReferenceStatus = "present"


class AgentTraceContextProjection(BaseModel):
    """Redacted Context v3 audit metadata; it never contains section bodies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_hash: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=64)
    required_sections: tuple[str, ...] = ()
    included_sections: tuple[str, ...] = ()
    omitted_sections: tuple[str, ...] = ()
    complete: bool
    incomplete_reason: str | None = Field(default=None, max_length=256)
    evidence_snapshot_hash: str | None = Field(default=None, max_length=128)
    projection_policy_version: str = Field(min_length=1, max_length=128)


class AgentTraceLifecycleEvent(BaseModel):
    """Redacted lifecycle reducer input retained in trace order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    from_state: str | None = Field(default=None, max_length=64)
    to_state: str = Field(min_length=1, max_length=64)
    source_command: str = Field(min_length=1, max_length=128)


class AgentTraceEntry(BaseModel):
    """One persisted Harness step and the safe references it consumed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str = Field(min_length=1, max_length=128)
    step_no: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=512)
    context_refs: tuple[AgentTraceReference, ...] = ()
    context_projection: AgentTraceContextProjection | None = None
    model_calls: tuple[ModelCallRecord, ...] = ()
    action_record: AgentActionRecord | None = None
    action_kind: str | None = Field(default=None, max_length=64)
    action_hash: str | None = Field(default=None, max_length=128)
    action_result_hash: str | None = Field(default=None, max_length=128)
    validation_result: Literal["accepted", "rejected", "error"]
    action_result_code: str | None = Field(default=None, max_length=128)
    state_before: str = Field(min_length=1, max_length=64)
    state_after: str | None = Field(default=None, max_length=64)
    started_at: datetime
    completed_at: datetime | None = None
    references: tuple[AgentTraceReference, ...] = ()


class AgentTraceBudget(BaseModel):
    """Attempt totals compared against replayed step/call ledgers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps_used: int = Field(ge=0)
    model_calls_used: int = Field(ge=0)
    action_proposals_used: int = Field(ge=0)
    repairs_used: int = Field(ge=0)
    input_tokens_used: int | None = Field(default=None, ge=0)
    output_tokens_used: int | None = Field(default=None, ge=0)


class AgentTraceBundle(BaseModel):
    """Redacted, canonical, read-only projection for one lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trace_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    lifecycle_id: str = Field(min_length=1, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)
    policy_version: str | None = Field(default=None, max_length=128)
    prompt_template_version: str | None = Field(default=None, max_length=128)
    skill_hashes: tuple[str, ...] = ()
    provider_refs: tuple[str, ...] = ()
    entries: tuple[AgentTraceEntry, ...] = ()
    lifecycle_events: tuple[AgentTraceLifecycleEvent, ...] = ()
    references: tuple[AgentTraceReference, ...] = ()
    budget: AgentTraceBudget | None = None
    initial_state: str | None = Field(default=None, max_length=64)
    final_state: str = Field(min_length=1, max_length=64)
    stop_reason: str | None = Field(default=None, max_length=128)
    integrity_status: TraceIntegrityStatus
    integrity_issues: tuple[str, ...] = ()
    integrity_hash: str = Field(min_length=1, max_length=128)


class AgentTracePage(BaseModel):
    """Paginated advanced-detail response; bundle metadata stays redacted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str
    project_id: str
    lifecycle_id: str
    integrity_status: TraceIntegrityStatus
    integrity_hash: str
    final_state: str
    stop_reason: str | None = None
    entries: tuple[AgentTraceEntry, ...]
    next_cursor: int | None = Field(default=None, ge=0)


class AgentReplayViolation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ReplayViolationCode
    entry_id: str | None = Field(default=None, max_length=128)
    message: str = Field(min_length=1, max_length=512)


class AgentReplayResult(BaseModel):
    """Pure replay verdict; no provider, handler, filesystem, or gateway work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trace_id: str
    integrity_valid: bool
    state_valid: bool
    budget_valid: bool
    final_state: str | None = None
    violations: tuple[AgentReplayViolation, ...] = ()
