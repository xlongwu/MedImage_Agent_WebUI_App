"""Data-free project health projection for the Agent control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentOperationalAttention(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    severity: Literal["warning", "blocking"]
    count: int = Field(ge=1)


class AgentOperationalSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    window_hours: int = Field(ge=1, le=720)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lifecycle_state_counts: dict[str, int]
    model_call_counts: dict[str, int]
    approval_waiting_count: int = Field(ge=0)
    attentions: tuple[AgentOperationalAttention, ...] = ()
    truncated: bool = False
