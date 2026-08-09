"""Immutable, redacted project evidence used by Agent planning."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EvidenceType = Literal[
    "project", "dataset", "artifacts", "plans", "runs", "observations", "memory", "capabilities"
]


class EvidenceSourceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=256)
    source_hash: str | None = Field(default=None, max_length=128)


class EvidenceFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    value: str | int | float | bool | None
    source_refs: tuple[EvidenceSourceRef, ...] = ()


class EvidenceWarning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=512)
    source_refs: tuple[EvidenceSourceRef, ...] = ()


class EvidenceSnapshot(BaseModel):
    """Persisted structured evidence only; never raw data, logs, or secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_hash: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1)
    lifecycle_id: str = Field(min_length=1)
    requested_types: tuple[EvidenceType, ...]
    facts: tuple[EvidenceFact, ...] = ()
    missing: tuple[str, ...] = ()
    warnings: tuple[EvidenceWarning, ...] = ()
    source_refs: tuple[EvidenceSourceRef, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
