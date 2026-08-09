"""Stable, portable summary for one bounded reviewed execution approval."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApprovalSummarySection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    summary: str
    warnings: tuple[str, ...] = ()


class ApprovalSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    summary_hash: str
    project_id: str
    reviewed_plan_id: str
    plan_hash: str
    planning_inputs_hash: str
    revision_no: int
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: str
    memory_context_hash: str | None = None
    memory_refs: tuple[dict[str, object], ...] = ()
    memory_influence_summary: tuple[str, ...] = ()
    goal_contract_hash: str
    goal: str
    dataset_summary: str
    execution_summary: str
    write_roots: tuple[str, ...]
    rawdata_read_only: bool = True
    node_ids: tuple[str, ...] = ()
    backend_ids: tuple[str, ...] = ()
    external_tools: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    science_changes: tuple[str, ...] = ()
    sections: tuple[ApprovalSummarySection, ...] = ()
    confirmations: dict[str, object]
    issued_at: datetime
    expires_at: datetime
