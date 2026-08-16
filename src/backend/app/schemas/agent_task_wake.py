"""Durable planning wake records owned by :class:`AgentTaskScheduler`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AgentTaskWakeStatus = Literal["PENDING", "CLAIMED", "CONSUMED", "RETRY"]


class AgentTaskWakeRecord(BaseModel):
    """One idempotent request to continue a persisted planning lifecycle.

    The payload deliberately contains only control-plane references.  It never
    carries model prompts, user data, execution tickets, or executable work.
    """

    model_config = ConfigDict(extra="forbid")

    wake_id: str
    project_id: str
    lifecycle_id: str
    step_key: str
    reason: str = Field(min_length=1, max_length=128)
    status: AgentTaskWakeStatus = "PENDING"
    attempts: int = Field(default=0, ge=0)
    available_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    last_error_code: str | None = None
    created_at: datetime
    updated_at: datetime
