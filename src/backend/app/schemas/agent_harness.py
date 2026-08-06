"""Persisted, bounded control-plane records for the optional Agent Harness.

The models in this module intentionally describe *advice* and its audit trail.
They carry no execution ticket, approval, runner, filesystem, or shell fields.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    schema_version: Literal[1] = 1
    attempt_id: str
    lifecycle_id: str
    project_id: str
    status: AgentHarnessStatus = "READY"
    mode: Literal["single_agent"] = "single_agent"
    provider_ref: str
    context_hash: str | None = None
    next_step_no: int = Field(default=1, ge=1)
    model_calls_used: int = Field(default=0, ge=0)
    tool_proposals_used: int = Field(default=0, ge=0)
    deadline_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    lease_takeovers: int = Field(default=0, ge=0)
    terminal_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentHarnessStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    step_id: str
    attempt_id: str
    project_id: str
    step_no: int = Field(ge=1)
    idempotency_key: str
    kind: AgentHarnessActionKind | None = None
    input_hash: str
    output_hash: str | None = None
    requested_capability: str | None = None
    validation_result: Literal["accepted", "rejected", "error"]
    state_before: str
    state_after: str | None = None
    summary: str = Field(default="", max_length=1024)
    started_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None


class AgentHarnessContext(BaseModel):
    """Immutable, redacted context snapshot used to call the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    context_hash: str
    lifecycle_id: str
    project_id: str
    allowed_fields_json: dict[str, Any]
    memory_context_hash: str | None = None
    project_snapshot_hash: str
    prompt_template_version: Literal[1] = 1
    omitted_fields: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentHarnessSummary(BaseModel):
    """Read-only, redacted projection exposed in Agent Task responses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AgentHarnessStatus
    model_calls_used: int = Field(ge=0)
    model_calls_limit: int = Field(ge=1)
    tool_proposals_used: int = Field(ge=0)
    tool_proposals_limit: int = Field(ge=1)
    next_step: str | None = None
    terminal_reason: str | None = None
    latest_step_id: str | None = None
    latest_step_summary: str | None = None
