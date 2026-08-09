"""Public, project-scoped Agent Task projection contracts.

These models are a read model over canonical lifecycle evidence. They are not
persisted and never become a second workflow state machine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.backend.app.schemas.agent_harness import AgentHarnessSummary

from src.backend.app.schemas.node_contract import CapabilityLevel

AgentTaskPublicState = Literal[
    "preparing",
    "waiting_for_user",
    "running",
    "needs_attention",
    "completed",
]
AgentTaskOutcome = Literal["succeeded", "partial", "failed", "canceled", "indeterminate"]
AgentTaskNextActionType = Literal[
    "none",
    "provide_input",
    "revise_goal",
    "answer_science_decision",
    "approve_execution",
    "approve_recovery",
    "review_results",
    "view_attention",
    "contact_support",
]
AgentTaskProgressPhase = Literal[
    "context",
    "planning",
    "plan_ready",
    "data_preparation",
    "execution",
    "validation",
    "recovery",
    "complete",
]
AgentTaskEvidenceType = Literal[
    "task_details",
    "reviewed_plan",
    "execution_ticket",
    "run",
    "observation",
    "goal_evaluation",
    "artifact",
    "validation",
    "provenance",
    "audit",
    "diagnosis",
    "recovery",
]
AgentTaskEventSource = Literal[
    "lifecycle",
    "reviewed_plan",
    "ticket",
    "run",
    "observation",
    "goal_evaluation",
    "diagnosis",
    "recovery",
    "artifact",
]


class AgentTaskNextAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: AgentTaskNextActionType
    title: str
    description: str | None = None
    requires_user: bool
    decision_batch_id: str | None = None
    disabled_reason: str | None = None


class AgentTaskProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: AgentTaskProgressPhase
    percent: int | None = Field(default=None, ge=0, le=100)
    completed_subjects: int | None = Field(default=None, ge=0)
    failed_subjects: int | None = Field(default=None, ge=0)
    excluded_subjects: int | None = Field(default=None, ge=0)
    total_subjects: int | None = Field(default=None, ge=0)


class AgentTaskDecisionOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    description: str
    recommended: bool = False


class AgentTaskDecision(BaseModel):
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
    impact: str
    options: tuple[AgentTaskDecisionOption, ...] = ()
    recommended_option: str | None = None
    source: Literal["planner", "memory_suggestion"] = "planner"
    memory_id: str | None = None
    recommendation_source: str | None = None
    answer_type: Literal["option", "text"] = "option"
    required: bool = True
    evidence_refs: tuple[str, ...] = ()


class AgentTaskDecisionBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: str
    evidence_snapshot_hash: str
    plan_hash_before: str | None = None
    expires_at: datetime


class AgentTaskApprovalSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    summary: str
    warnings: tuple[str, ...] = ()


class AgentTaskApprovalSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary_hash: str
    goal: str
    dataset_summary: str
    execution_summary: str
    write_roots: tuple[str, ...]
    rawdata_read_only: bool = True
    external_tools: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    science_changes: tuple[str, ...] = ()
    memory_context_hash: str | None = None
    memory_refs: tuple[dict[str, object], ...] = ()
    memory_influence_summary: tuple[str, ...] = ()
    planning_inputs_hash: str | None = None
    revision_no: int | None = Field(default=None, ge=1)
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: str | None = None
    sections: tuple[AgentTaskApprovalSection, ...] = ()
    expires_at: datetime | None = None


class AgentTaskArtifactSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    artifact_type: str
    label: str
    uri: str
    checksum: str | None = None
    capability_level: CapabilityLevel
    reload_status: Literal["not_checked", "passed", "failed", "unavailable"]


class AgentTaskResultSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: AgentTaskOutcome
    title: str
    summary: str
    qc_summary: str | None = None
    completed_subjects: int | None = Field(default=None, ge=0)
    failed_subjects: int | None = Field(default=None, ge=0)
    excluded_subjects: int | None = Field(default=None, ge=0)
    total_subjects: int | None = Field(default=None, ge=0)
    limitations: tuple[str, ...] = ()
    recommended_action: str | None = None
    artifacts: tuple[AgentTaskArtifactSummary, ...] = ()


class AgentResultCriterion(BaseModel):
    """Read-only criterion outcome copied from the deterministic evaluator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_id: str
    status: Literal["passed", "failed", "indeterminate"]
    reason_code: str
    evidence_ids: tuple[str, ...] = ()


class AgentResultExplanation(BaseModel):
    """Structured result explanation; generated prose never controls the outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: AgentTaskOutcome
    completed_subjects: int | None = Field(default=None, ge=0)
    failed_subjects: int | None = Field(default=None, ge=0)
    excluded_subjects: int | None = Field(default=None, ge=0)
    total_subjects: int | None = Field(default=None, ge=0)
    artifact_refs: tuple[AgentTaskArtifactSummary, ...] = ()
    criteria: tuple[AgentResultCriterion, ...] = ()
    limitations: tuple[str, ...] = ()
    recommended_action: str | None = None
    generated_text: str | None = Field(default=None, max_length=2048)
    generated_text_status: Literal["not_requested", "accepted", "conflict_rejected"] = "not_requested"


class AgentTaskRecoverySummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    diagnosis: str
    affected_subjects: tuple[str, ...] = ()
    recommended_action: str
    untouched_scope: tuple[str, ...] = ()
    requires_new_plan: bool
    approval_summary_hash: str | None = None


class AgentTaskEvidenceLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    type: AgentTaskEvidenceType
    label: str
    uri: str
    available: bool


class AgentTaskBackendSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requested: str
    selected: str | None = None
    fallback_reason: str | None = None


class AgentTaskTechnicalDetails(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lifecycle_id: str
    internal_state: str
    reviewed_plan_id: str | None = None
    plan_hash: str | None = None
    planning_inputs_hash: str | None = None
    plan_revision_no: int | None = Field(default=None, ge=1)
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: str | None = None
    evidence_snapshot_hash: str | None = None
    goal_contract_id: str | None = None
    goal_hash: str | None = None
    ticket_id: str | None = None
    run_id: str | None = None
    observation_id: str | None = None
    evaluation_id: str | None = None
    backend: AgentTaskBackendSelection | None = None
    node_ids: tuple[str, ...] = ()
    memory_context_hash: str | None = None
    memory_refs: tuple[dict[str, object], ...] = ()
    memory_retrieval_policy_version: str | None = None
    memory_status: Literal["disabled", "enabled", "partial"] | None = None
    memory_used_bytes: int | None = Field(default=None, ge=0)
    memory_omitted_count: int | None = Field(default=None, ge=0)
    memory_warnings: tuple[str, ...] = ()
    memory_available: bool | None = None
    memory_generate_enabled: bool | None = None
    memory_use_enabled: bool | None = None


class AgentTaskResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    task_id: str
    project_id: str
    state: AgentTaskPublicState
    outcome: AgentTaskOutcome | None = None
    goal_summary: str
    current_action: str
    next_action: AgentTaskNextAction
    progress: AgentTaskProgress
    decisions: tuple[AgentTaskDecision, ...] = ()
    decision_batch: AgentTaskDecisionBatch | None = None
    approval_summary: AgentTaskApprovalSummary | None = None
    result_summary: AgentTaskResultSummary | None = None
    result_explanation: AgentResultExplanation | None = None
    recovery: AgentTaskRecoverySummary | None = None
    evidence_links: tuple[AgentTaskEvidenceLink, ...] = ()
    technical_details: AgentTaskTechnicalDetails | None = None
    harness_summary: AgentHarnessSummary | None = None
    created_at: datetime
    updated_at: datetime


class AgentTaskListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    items: tuple[AgentTaskResponse, ...]
    total: int = Field(ge=0)


class AgentTaskEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    task_id: str
    project_id: str
    source: AgentTaskEventSource
    type: str
    occurred_at: datetime
    title: str
    summary: str
    evidence_uri: str | None = None


class AgentTaskEventPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    items: tuple[AgentTaskEvent, ...]
    next_cursor: str | None = None


class CreateAgentTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)


class DecisionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    value: str = Field(min_length=1)


class AnswerAgentTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1)
    answers: tuple[DecisionAnswer, ...] = Field(min_length=1, max_length=6)
    command_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)


class ApproveAgentTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_summary_hash: str = Field(min_length=1)
    command_id: str = Field(min_length=1)


class CancelAgentTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    reason: str | None = None


class ApproveAgentTaskRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
