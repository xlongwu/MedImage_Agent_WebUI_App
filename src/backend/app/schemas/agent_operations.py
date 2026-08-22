"""Data-free project health projection for the Agent control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentOperationalAttention(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    severity: Literal["info", "warning", "blocking"]
    count: int = Field(ge=1)
    related_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=20)


class AgentOperationalSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    project_id: str
    window_started_at: datetime
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    truncated: bool = False
    task_counts: dict[str, int]
    model_call_counts: dict[str, int]
    provider_failure_counts: dict[str, int]
    scheduler_counts: dict[str, int]
    approval_counts: dict[str, int]
    gateway_counts: dict[str, int]
    sandbox_counts: dict[str, int]
    memory_status: str
    latency_ms: dict[str, int | float | None]
    attention: tuple[AgentOperationalAttention, ...] = ()
