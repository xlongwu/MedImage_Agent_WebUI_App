"""Read-only consistency findings for the persisted Agent control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentInvariantFinding(BaseModel):
    """A redacted, machine-readable mismatch between authoritative records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    severity: Literal["warning", "blocking"]
    lifecycle_id: str = Field(min_length=1, max_length=128)
    related_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    message_key: str = Field(min_length=1, max_length=128)
    evidence_hashes: tuple[str, ...] = Field(default_factory=tuple, max_length=12)


class AgentInvariantReport(BaseModel):
    """One bounded diagnostic pass; no context, paths, or data are exposed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    lifecycle_id: str
    project_id: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    findings: tuple[AgentInvariantFinding, ...] = ()

    @property
    def blocking(self) -> tuple[AgentInvariantFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "blocking")


class AgentInvariantAuditRecord(BaseModel):
    """Minimal immutable audit projection for an explicit diagnostic request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str
    lifecycle_id: str
    project_id: str
    report_hash: str
    finding_codes: tuple[str, ...]
    blocking_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
