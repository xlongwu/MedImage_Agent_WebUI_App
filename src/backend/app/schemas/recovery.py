"""Immutable contracts for recovery diagnosis and proposal-only decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RecoveryAction = Literal[
    "SAFE_RETRY",
    "RETRY_FAILED_SUBJECTS",
    "RESUME",
    "PARAMETER_CHANGE",
    "BACKEND_SWITCH",
    "REPLAN",
    "HUMAN_HANDOFF",
]
RecoveryRisk = Literal["low", "medium", "high", "unknown"]
ApprovalClass = Literal[
    "explicit_retry_approval",
    "explicit_resume_approval",
    "new_reviewed_plan_and_approval",
    "human_handoff",
]
RootCauseStatus = Literal["known", "probable", "unknown"]
GapStatus = Literal["failed", "indeterminate"]


class RecoveryBindings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    lifecycle_id: str
    reviewed_plan_id: str
    plan_hash: str
    execution_ticket_id: str
    run_id: str
    goal_contract_id: str
    goal_contract_hash: str
    observation_id: str
    observation_hash: str
    goal_evaluation_id: str
    goal_evaluation_hash: str


class DiagnosisFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    category: str
    scope: Literal["project", "node", "subject", "session", "artifact", "validation"]
    severity: Literal["info", "warning", "error", "blocking"] = "error"
    node_id: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    artifact_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    confidence_source: Literal[
        "explicit_state",
        "contract_rule",
        "validator",
        "legacy_classifier",
    ]
    retryability: Literal["retryable", "non_retryable", "unknown"] = "unknown"
    message: str


class GoalGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_id: str
    criterion_type: str
    status: GapStatus
    reason_code: str
    expected: Any = None
    actual: Any = None
    evidence_ids: tuple[str, ...] = ()
    affected_nodes: tuple[str, ...] = ()
    affected_subjects: tuple[str, ...] = ()
    affected_artifacts: tuple[str, ...] = ()


class DiagnosisSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis_id: str
    diagnosis_hash: str
    root_cause_status: RootCauseStatus
    fact_count: int
    gap_count: int
    blocking_safety_issues: tuple[str, ...] = ()


class DiagnosisRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis_id: str
    schema_version: Literal[1] = 1
    diagnoser_version: str = "run-diagnosis-v1"
    bindings: RecoveryBindings
    created_at: datetime
    facts: tuple[DiagnosisFact, ...]
    goal_gaps: tuple[GoalGap, ...]
    root_cause_status: RootCauseStatus
    blocking_safety_issues: tuple[str, ...] = ()
    legacy_source_ref: str | None = None
    diagnosis_hash: str

    def summary(self) -> DiagnosisSummary:
        return DiagnosisSummary(
            diagnosis_id=self.diagnosis_id,
            diagnosis_hash=self.diagnosis_hash,
            root_cause_status=self.root_cause_status,
            fact_count=len(self.facts),
            gap_count=len(self.goal_gaps),
            blocking_safety_issues=self.blocking_safety_issues,
        )


class RecoveryQuotaLimits(BaseModel):
    """All fields are optional at ingestion; missing is normalized to hard zero."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_lifecycle_recovery_attempts: int | None = Field(default=None, ge=0)
    max_node_attempts: int | None = Field(default=None, ge=0)
    max_subject_node_attempts: int | None = Field(default=None, ge=0)
    max_replans: int | None = Field(default=None, ge=0)
    max_recovery_wall_seconds: int | None = Field(default=None, ge=0)


class RecoveryQuotaUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lifecycle_recovery_attempts: int = Field(default=0, ge=0)
    node_attempts: int = Field(default=0, ge=0)
    subject_node_attempts: int = Field(default=0, ge=0)
    replans: int = Field(default=0, ge=0)
    recovery_wall_seconds: int = Field(default=0, ge=0)


class RecoveryQuotaSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: Literal["ticket", "node_contract", "project_policy"]
    source_id: str
    limits: RecoveryQuotaLimits


class RecoveryQuotaDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[RecoveryQuotaSource, ...]
    effective_limits: dict[str, int]
    usage: RecoveryQuotaUsage
    missing_dimensions: tuple[str, ...] = ()
    exhausted_dimensions: tuple[str, ...] = ()
    executable: bool
    reason_codes: tuple[str, ...] = ()


DiffDimension = Literal[
    "normalized_params",
    "node_ids",
    "contract_versions",
    "dag_dependencies",
    "backend_ids",
    "execution_backend_policy",
    "roots",
    "scope",
    "artifact_types",
    "output_policy",
    "goal_contract",
    "approval_context",
    "safe_allowlist",
]


class CanonicalDiffEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: DiffDimension
    before_hash: str
    after_hash: str
    changed: bool
    classification: Literal[
        "unchanged",
        "same_reviewed_contract",
        "new_reviewed_plan",
        "blocked",
    ]
    details: dict[str, Any] = Field(default_factory=dict)


class CanonicalRecoveryDiff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[CanonicalDiffEntry, ...]
    changes_reviewed_contract: bool
    unclassified_dimensions: tuple[str, ...] = ()
    canonical_diff_hash: str


class RecoveryExecutionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    normalized_params: dict[str, Any]
    node_ids: tuple[str, ...]
    contract_versions: tuple[tuple[str, str], ...]
    dag_dependencies: tuple[tuple[str, tuple[str, ...]], ...]
    backend_ids: tuple[tuple[str, str], ...]
    execution_backend_policy: dict[str, Any]
    input_roots: tuple[str, ...]
    output_roots: tuple[str, ...]
    readonly_roots: tuple[str, ...]
    subject_scope: tuple[str, ...]
    session_scope: tuple[str, ...]
    output_scope: tuple[str, ...]
    artifact_types: tuple[tuple[str, tuple[str, ...]], ...]
    output_policy: tuple[tuple[str, str, str], ...]
    goal_contract_hash: str
    approval_summary_hash: str
    allowlist_hash: str


class CheckpointEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint_id: str
    schema_id: str
    verified: bool = False
    plan_hash: str
    normalized_params_hash: str
    backend_ids: tuple[str, ...]
    input_roots: tuple[str, ...]
    output_roots: tuple[str, ...]
    completed_node_ids: tuple[str, ...] = ()
    remaining_node_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class RecoveryChangeRequest(BaseModel):
    """Review-only candidate changes; never an execution instruction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameter_patch: dict[str, dict[str, Any]] = Field(default_factory=dict)
    backend_patch: dict[str, str] = Field(default_factory=dict)
    replacement_node_ids: tuple[str, ...] | None = None
    dag_patch: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    input_roots: tuple[str, ...] | None = None
    output_roots: tuple[str, ...] | None = None
    readonly_roots: tuple[str, ...] | None = None
    subject_scope: tuple[str, ...] | None = None
    session_scope: tuple[str, ...] | None = None
    output_scope: tuple[str, ...] | None = None
    goal_contract_hash: str | None = None
    approval_summary_hash: str | None = None
    allowlist_hash: str | None = None


class RecoveryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    candidate_hash: str
    action: RecoveryAction
    scope: Literal["project", "nodes", "subjects", "checkpoint", "reviewed_plan", "human"]
    target_node_ids: tuple[str, ...] = ()
    target_subject_ids: tuple[str, ...] = ()
    checkpoint_id: str | None = None
    checkpoint_evidence: CheckpointEvidence | None = None
    parameter_patch: dict[str, dict[str, Any]] = Field(default_factory=dict)
    backend_patch: dict[str, str] = Field(default_factory=dict)
    change_request: RecoveryChangeRequest | None = None
    canonical_diff: CanonicalRecoveryDiff
    risk: RecoveryRisk
    idempotency: Literal["idempotent", "isolated_output", "unsafe", "not_applicable"]
    expected_evidence: tuple[str, ...] = ()
    approval_class: ApprovalClass
    reason_codes: tuple[str, ...]
    blocked_reasons: tuple[str, ...] = ()
    safe_human_actions: tuple[str, ...] = ()
    eligible: bool
    executable: bool
    changes_reviewed_plan: bool
    rank_key: tuple[int, ...]

    @model_validator(mode="after")
    def enforce_handoff_and_plan_actions(self) -> RecoveryCandidate:
        if self.action == "HUMAN_HANDOFF" and self.executable:
            raise ValueError("HUMAN_HANDOFF never has execution capability")
        if self.action == "HUMAN_HANDOFF" and not self.safe_human_actions:
            raise ValueError("HUMAN_HANDOFF requires non-executable safe actions")
        if self.action in {"PARAMETER_CHANGE", "BACKEND_SWITCH", "REPLAN"}:
            if not self.changes_reviewed_plan or self.executable:
                raise ValueError("plan-changing recovery is review-only")
        if self.action == "RESUME":
            if self.checkpoint_evidence is None or self.checkpoint_id != self.checkpoint_evidence.checkpoint_id:
                raise ValueError("RESUME requires hash-bound checkpoint evidence")
        return self


class RecoveryProposalSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recovery_proposal_id: str
    recovery_proposal_hash: str
    recommended_candidate_id: str
    recommended_action: RecoveryAction
    candidate_count: int
    executable_candidate_count: int


class RecoveryProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recovery_proposal_id: str
    schema_version: Literal[1] = 1
    engine_version: str = "recovery-proposal-v1"
    bindings: RecoveryBindings
    diagnosis_id: str
    diagnosis_hash: str
    created_at: datetime
    parent_recovery_proposal_id: str | None = None
    quota: RecoveryQuotaDecision
    candidates: tuple[RecoveryCandidate, ...]
    recommended_candidate_id: str
    recovery_proposal_hash: str

    @model_validator(mode="after")
    def recommended_candidate_must_be_eligible(self) -> RecoveryProposal:
        matches = [
            candidate
            for candidate in self.candidates
            if candidate.candidate_id == self.recommended_candidate_id
        ]
        if len(matches) != 1 or not matches[0].eligible:
            raise ValueError("recommended candidate must be one eligible candidate")
        return self

    def summary(self) -> RecoveryProposalSummary:
        recommended = next(
            item for item in self.candidates if item.candidate_id == self.recommended_candidate_id
        )
        return RecoveryProposalSummary(
            recovery_proposal_id=self.recovery_proposal_id,
            recovery_proposal_hash=self.recovery_proposal_hash,
            recommended_candidate_id=self.recommended_candidate_id,
            recommended_action=recommended.action,
            candidate_count=len(self.candidates),
            executable_candidate_count=sum(item.executable for item in self.candidates),
        )
