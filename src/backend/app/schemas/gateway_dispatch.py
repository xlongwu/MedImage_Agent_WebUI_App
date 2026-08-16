"""Immutable gateway dispatch identity and append-only outcome events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


GatewayDispatchEventType = Literal[
    "dispatch_started",
    "dispatch_succeeded",
    "dispatch_failed",
    "dispatch_rejected",
]


class GatewayDispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    dispatch_id: str
    command_id: str
    project_id: str
    reviewed_plan_id: str
    execution_ticket_id: str
    approval_summary_hash: str
    execution_environment_snapshot_id: str
    execution_environment_hash: str
    plan_hash: str
    memory_context_hash: str | None = None
    scope_hash: str
    allowlist_hash: str
    run_id: str
    created_at: datetime
    canonical_hash: str


class GatewayDispatchEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    event_id: str
    dispatch_id: str
    project_id: str
    event_type: GatewayDispatchEventType
    occurred_at: datetime
    failure_code: str | None = None
    result_hash: str | None = None
    redacted_summary: str = Field(default="", max_length=512)
    result: dict[str, Any] | None = None
