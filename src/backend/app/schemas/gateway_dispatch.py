"""Immutable gateway dispatch identity and append-only outcome events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


GatewayDispatchEventType = Literal[
    "sandbox_prepared",
    "dispatch_started",
    "dispatch_succeeded",
    "dispatch_failed",
    "dispatch_rejected",
]


class GatewayDispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 2
    dispatch_id: str
    command_id: str
    project_id: str
    reviewed_plan_id: str
    execution_ticket_id: str
    approval_summary_hash: str
    execution_environment_snapshot_id: str
    execution_environment_hash: str
    sandbox_policies_hash: str = "2fa91d28b8039d17bb1463c12c1d7823b8e474ae7d9c7d0109b36f17283f04bb"
    sandbox_provider: str = "windows_restricted_process"
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
