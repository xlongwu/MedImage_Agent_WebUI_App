"""Persistable, redacted evidence for one planner invocation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlannerInvocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    invocation_id: str
    provider_id: str
    model_id: str
    prompt_template_version: str
    prompt_template_hash: str
    input_schema_version: str
    input_hash: str
    started_at: datetime
    timeout_ms: int = Field(ge=1)


class PlannerEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    invocation_id: str
    output_hash: str | None = None
    validation_codes: tuple[str, ...] = ()
    fallback_used: bool = False
    failure_code: str | None = None
    redacted_summary: str = Field(default="", max_length=512)
