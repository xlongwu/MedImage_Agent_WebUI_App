"""Versioned persisted state for the controlled Agent workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.backend.app.schemas.goal_contract import GoalEvaluationSummary
from src.backend.app.schemas.observation import ObservationSummary
from src.backend.app.schemas.recovery import DiagnosisSummary, RecoveryProposalSummary

AgentLifecycleState = Literal[
    "CREATED",
    "WAITING_FOR_INPUT",
    "CONTEXT_READY",
    "PLAN_DRAFTED",
    "WAITING_FOR_SCIENCE_DECISION",
    "PLAN_VALIDATED",
    "WAITING_FOR_APPROVAL",
    "APPROVED",
    "EXECUTION_READY",
    "RUNNING",
    "OBSERVING",
    "EVALUATING",
    "GOAL_SATISFIED",
    "SUCCEEDED",
    "FAILED",
    "DIAGNOSING",
    "RETRY_PROPOSED",
    "WAITING_FOR_RETRY_APPROVAL",
    "RETRYING",
    "RECOVERY_PROPOSED",
    "WAITING_FOR_RECOVERY_APPROVAL",
    "RECOVERY_READY",
    "RECOVERING",
    "HUMAN_HANDOFF",
    "CANCELED",
]


class PendingDecisionOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    description: str
    recommended: bool = False


class DecisionItem(BaseModel):
    """One explicit user choice within an atomic decision batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    kind: Literal[
        "missing_input",
        "goal_revision",
        "subject_id",
        "atlas",
        "global_signal_regression",
        "repetition_time",
        "template",
        "overwrite",
        "experimental_backend",
        "other",
    ]
    question: str
    options: tuple[PendingDecisionOption, ...] = ()
    recommended_option: str | None = None
    impact: str
    source: Literal["planner", "memory_suggestion"] = "planner"
    memory_id: str | None = None
    recommendation_source: str | None = None
    answer_type: Literal["option", "boolean", "number", "text"] = "option"
    min_value: float | None = None
    max_value: float | None = None
    required: bool = True
    evidence_refs: tuple[str, ...] = ()


class PendingDecisionBatch(BaseModel):
    """The sole unresolved decision object for a lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: str
    lifecycle_id: str
    project_id: str
    evidence_snapshot_hash: str
    plan_hash_before: str | None = None
    items: tuple[DecisionItem, ...] = Field(min_length=1, max_length=6)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    source: Literal["planner", "memory_suggestion", "harness"] = "planner"

    @model_validator(mode="after")
    def unique_items(self) -> "PendingDecisionBatch":
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("AGENT_DECISION_BATCH_DUPLICATE_ITEM")
        return self


class RetryProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: str
    node_ids: tuple[str, ...]
    backend_ids: tuple[str, ...]
    parameter_hash: str
    input_roots: tuple[str, ...]
    output_roots: tuple[str, ...]
    classifier: str
    risk: Literal["low", "high", "unknown"] = "unknown"
    requires_approval: bool = True
    changes_reviewed_contract: bool = False


class AgentLifecycleRecord(BaseModel):
    """Canonical task state, bound only to separately persisted observations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[5] = 5
    lifecycle_id: str
    project_id: str
    state: AgentLifecycleState = "CREATED"
    goal_text: str | None = None
    goal_hash: str | None = None
    created_actor: str | None = None
    command_context: dict[str, Any] = Field(default_factory=dict)
    pending_decision_batch: PendingDecisionBatch | None = None
    evidence_snapshot_hash: str | None = None
    reviewed_plan_id: str | None = None
    execution_ticket_id: str | None = None
    parent_execution_ticket_id: str | None = None
    audit_id: str | None = None
    run_id: str | None = None
    parent_run_id: str | None = None
    goal_contract_id: str | None = None
    goal_contract_hash: str | None = None
    goal_evaluation_id: str | None = None
    goal_evaluation_summary: GoalEvaluationSummary | None = None
    diagnosis_id: str | None = None
    diagnosis_summary: DiagnosisSummary | None = None
    recovery_proposal_id: str | None = None
    recovery_proposal_summary: RecoveryProposalSummary | None = None
    recovery_approval_id: str | None = None
    recovery_attempt_id: str | None = None
    retry_count: int = 0
    retry_quota: int = 0
    observation_id: str | None = None
    observation_summary: ObservationSummary | None = None
    retry_proposal: RetryProposal | None = None
    last_error: str | None = None
    canceled_at: datetime | None = None
    canceled_by: str | None = None
    cancellation_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_command_id: str | None = None

class AgentLifecycleEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    event_id: str
    lifecycle_id: str
    project_id: str
    command_id: str
    actor: str
    source_command: str
    occurred_at: datetime
    from_state: AgentLifecycleState | None
    to_state: AgentLifecycleState
    reviewed_plan_id: str | None = None
    execution_ticket_id: str | None = None
    recovery_approval_id: str | None = None
    recovery_attempt_id: str | None = None
    audit_id: str | None = None
    run_id: str | None = None
    observation_id: str | None = None
    goal_contract_id: str | None = None
    goal_evaluation_id: str | None = None
    diagnosis_id: str | None = None
    recovery_proposal_id: str | None = None
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LifecycleCommand(BaseModel):
    command_id: str
    action: str
    actor: str
    reason: str | None = None
    reviewed_plan_id: str | None = None
    execution_ticket_id: str | None = None
    audit_id: str | None = None
    run_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LifecycleCreateRequest(BaseModel):
    command_id: str
    actor: str
