"""Durable post-dispatch coordination records.

Execution coordination is deliberately distinct from the planning outbox: it
may only read run evidence and advance the lifecycle's observation/evaluation
chain.  It never receives a planner, provider, ticket issuer, or gateway.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AgentExecutionWakeStatus = Literal["PENDING", "CLAIMED", "CONSUMED", "RETRY"]


class AgentExecutionWakeRecord(BaseModel):
    """One idempotent check of an already-bound lifecycle/run pair."""

    model_config = ConfigDict(extra="forbid")

    wake_id: str
    project_id: str
    lifecycle_id: str
    run_id: str
    status: AgentExecutionWakeStatus = "PENDING"
    attempts: int = Field(default=0, ge=0)
    available_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    last_error_code: str | None = None
    created_at: datetime
    updated_at: datetime
