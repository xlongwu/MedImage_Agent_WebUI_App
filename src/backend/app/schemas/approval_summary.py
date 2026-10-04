"""Stable, portable summary for one bounded reviewed execution approval."""

from __future__ import annotations

from .sandbox import SANDBOX_POLICY_VERSION, SandboxPolicyVersion

from datetime import datetime
from typing import Literal

from src.backend.app.schemas.system_message import SystemMessage

from pydantic import BaseModel, ConfigDict, Field


class ApprovalSummarySection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: SystemMessage
    summary: SystemMessage
    warnings: tuple[SystemMessage, ...] = ()


class ApprovalSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[6] = 6
    summary_hash: str
    project_id: str
    reviewed_plan_id: str
    plan_hash: str
    execution_environment_snapshot_id: str
    execution_environment_hash: str
    sandbox_policies_hash: str = "2fa91d28b8039d17bb1463c12c1d7823b8e474ae7d9c7d0109b36f17283f04bb"
    sandbox_policy_version: SandboxPolicyVersion = SANDBOX_POLICY_VERSION
    sandbox_policies: tuple[dict[str, object], ...] = ()
    planning_inputs_hash: str
    evidence_snapshot_hash: str | None = None
    science_answers_hash: str
    planner_provider_ref: str
    planner_prompt_version: str
    model_profile_hash: str
    planner_skill_hashes: tuple[str, ...] = ()
    normalized_plan_hash: str
    plan_payload_hash: str
    revision_no: int
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: str
    memory_context_hash: str | None = None
    memory_refs: tuple[dict[str, object], ...] = ()
    memory_influence_summary: tuple[SystemMessage, ...] = ()
    goal_contract_hash: str
    goal: str
    registered_subject_count: int = Field(ge=0)
    selected_subject_ids: tuple[str, ...] = ()
    write_roots: tuple[str, ...]
    rawdata_read_only: bool = True
    node_ids: tuple[str, ...] = ()
    backend_ids: tuple[str, ...] = ()
    external_tools: tuple[str, ...] = ()
    limitations: tuple[SystemMessage, ...] = ()
    science_changes: tuple[SystemMessage, ...] = ()
    resource_policy: dict[str, object] = Field(default_factory=dict)
    sections: tuple[ApprovalSummarySection, ...] = ()
    confirmations: dict[str, object]
    issued_at: datetime
    expires_at: datetime
