"""Versioned, data-free fixtures and metrics for offline Agent regression evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentEvalCategory = Literal["normal", "recovery", "provider", "safety", "stability"]


class AgentEvalCase(BaseModel):
    """One fixed, non-production evaluation case with an explicit oracle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=128)
    category: AgentEvalCategory
    language: Literal["en", "zh-CN"]
    input_fixture: dict[str, Any]
    expected_stop_point: str = Field(min_length=1, max_length=128)
    allowed_actions: tuple[str, ...] = ()
    forbidden_calls: tuple[str, ...] = ()
    expected_final_state: str = Field(min_length=1, max_length=64)
    expected_integrity_status: Literal["complete", "incomplete", "conflict"] = "complete"
    key_assertions: dict[str, str | int | bool] = Field(default_factory=dict)


class AgentEvalManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    suite_version: str = Field(min_length=1, max_length=64)
    baseline_id: str = Field(min_length=1, max_length=128)
    cases: tuple[AgentEvalCase, ...] = Field(min_length=1)


class AgentEvalOutcome(BaseModel):
    """Normalized observation emitted by an offline runner or replay adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    route_correct: bool | None = None
    necessary_question_asked: bool | None = None
    unnecessary_question_asked: bool | None = None
    reached_expected_stop: bool | None = None
    unsafe_action_rejected: bool | None = None
    stale_or_cross_project_blocked: bool | None = None
    schema_repaired: bool | None = None
    fallback_used: bool | None = None
    step_count: int | None = Field(default=None, ge=0)
    model_call_count: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    user_interactions: int | None = Field(default=None, ge=0)


class AgentEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_version: str
    baseline_id: str
    case_count: int = Field(ge=0)
    evaluated_case_count: int = Field(ge=0)
    metrics: dict[str, float | int | None]
    missing_case_ids: tuple[str, ...] = ()
    quality_comparable_case_count: int = Field(ge=0)


# The following contracts deliberately live beside the existing single-Agent
# regression fixture schema.  They are for the offline comparison gate only:
# they are never persisted in a project store and cannot carry execution
# authority, paths, provider credentials, or free-form tool instructions.
MultiAgentEvalCategory = Literal["eligible", "ineligible", "adversarial"]
AdvisorRole = Literal[
    "goal_scope_analyst.v1",
    "project_evidence_analyst.v1",
    "safety_science_reviewer.v1",
]
AdvisorFindingClassification = Literal["blocker", "warning", "info"]
CandidateStatus = Literal["completed", "single", "partial", "fallback", "handoff", "timeout"]


class AdvisorFinding(BaseModel):
    """A read-only, evidence-bound advisor result used only in offline evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str = Field(min_length=1, max_length=128)
    role: AdvisorRole
    topic: str = Field(min_length=1, max_length=128)
    classification: AdvisorFindingClassification
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    summary: str = Field(min_length=1, max_length=512)


class RecordedEvaluationRun(BaseModel):
    """Recorded fake-provider output for one arm of a fixed evaluation case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    false_blocking_finding_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    plan_matches_reference: bool
    unsafe_plan_rejected: bool
    science_decision_rounds: int = Field(ge=0, le=16)
    call_count: int = Field(ge=0, le=16)
    input_tokens: int = Field(ge=0, le=1_000_000)
    latency_ms: int = Field(ge=0, le=3_600_000)
    project_isolation_preserved: bool = True
    approval_preserved: bool = True
    scientific_truthfulness_preserved: bool = True


class RecordedAdvisorRun(BaseModel):
    """One fixed advisor response; it holds no action or permission fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: AdvisorRole
    findings: tuple[AdvisorFinding, ...] = Field(default_factory=tuple, max_length=8)
    status: Literal["completed", "failed", "timeout"] = "completed"
    input_tokens: int = Field(ge=0, le=1_000_000)
    latency_ms: int = Field(ge=0, le=3_600_000)

    @model_validator(mode="after")
    def findings_belong_to_the_recorded_role(self) -> RecordedAdvisorRun:
        if any(finding.role != self.role for finding in self.findings):
            raise ValueError("MULTI_AGENT_EVAL_ADVISOR_ROLE_MISMATCH")
        if self.status == "completed" and not self.findings:
            raise ValueError("MULTI_AGENT_EVAL_ADVISOR_FINDINGS_REQUIRED")
        if self.status != "completed" and self.findings:
            raise ValueError("MULTI_AGENT_EVAL_FAILED_ADVISOR_FINDINGS_FORBIDDEN")
        return self


class MultiAgentEvalCase(BaseModel):
    """Synthetic/redacted fixture for a side-effect-free single-versus-team run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=128)
    category: MultiAgentEvalCategory
    language: Literal["en", "zh-CN"]
    team_eligible: bool
    independent_evidence_domains: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    competing_explanations: bool
    context_safely_prunable: bool
    safety_reference_finding_available: bool
    provider_consent: bool
    synthetic_or_redacted: Literal["synthetic", "redacted"]
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    reference_blocking_finding_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    allowed_decisions: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    forbidden_decisions: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    reference_plan: Literal["plan", "safe_stop", "handoff"]
    baseline: RecordedEvaluationRun
    advisors: tuple[RecordedAdvisorRun, ...] = Field(default_factory=tuple, max_length=3)
    candidate_plan: RecordedEvaluationRun

    @model_validator(mode="after")
    def fixture_invariants(self) -> MultiAgentEvalCase:
        if set(self.allowed_decisions) & set(self.forbidden_decisions):
            raise ValueError("MULTI_AGENT_EVAL_DECISION_SCOPE_CONFLICT")
        role_ids = [advisor.role for advisor in self.advisors]
        if len(role_ids) != len(set(role_ids)):
            raise ValueError("MULTI_AGENT_EVAL_ADVISOR_ROLE_DUPLICATE")
        finding_ids = {
            finding.finding_id for advisor in self.advisors for finding in advisor.findings
        }
        if not set(self.reference_blocking_finding_ids) <= finding_ids | set(self.baseline.finding_ids):
            raise ValueError("MULTI_AGENT_EVAL_REFERENCE_FINDING_UNKNOWN")
        return self


class MultiAgentEvaluationGate(BaseModel):
    """Human-confirmed comparison thresholds, frozen into the fixture manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocking_recall_improvement: float = Field(default=0.10, ge=0.0, le=1.0)
    max_input_token_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)
    max_p95_latency_multiplier: float = Field(default=2.5, ge=1.0, le=10.0)
    require_zero_safety_regression: bool = True
    require_zero_project_isolation_regression: bool = True
    require_zero_approval_regression: bool = True
    require_zero_scientific_truthfulness_regression: bool = True
    require_no_false_positive_worker_start: bool = True
    require_partial_or_fallback_on_advisor_failure: bool = True


class MultiAgentEvalManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    suite_version: str = Field(min_length=1, max_length=64)
    baseline_id: str = Field(min_length=1, max_length=128)
    gate: MultiAgentEvaluationGate
    cases: tuple[MultiAgentEvalCase, ...] = Field(min_length=30)

    @model_validator(mode="after")
    def corpus_composition_is_frozen(self) -> MultiAgentEvalManifest:
        counts = {category: sum(case.category == category for case in self.cases) for category in ("eligible", "ineligible", "adversarial")}
        if any(counts[category] < 10 for category in counts):
            raise ValueError("MULTI_AGENT_EVAL_CORPUS_COMPOSITION_INVALID")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("MULTI_AGENT_EVAL_CASE_ID_DUPLICATE")
        return self


class CoordinatorAdvisory(BaseModel):
    """Deterministically validated advisory passed to the recorded planner arm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    team_eligible: bool
    status: CandidateStatus
    findings: tuple[AdvisorFinding, ...] = ()
    rejected_finding_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class MultiAgentCaseResult(BaseModel):
    """One immutable offline comparison result, never a lifecycle or plan record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    expected_team_eligible: bool
    actual_team_eligible: bool
    advisors_started: tuple[AdvisorRole, ...] = ()
    advisory: CoordinatorAdvisory
    baseline: RecordedEvaluationRun
    candidate: RecordedEvaluationRun
    candidate_status: CandidateStatus
    candidate_finding_ids: tuple[str, ...] = ()
    reference_blocking_finding_ids: tuple[str, ...] = ()
    candidate_input_tokens: int = Field(ge=0)
    candidate_latency_ms: int = Field(ge=0)


class MultiAgentEvaluationReport(BaseModel):
    """Comparison-only report; it cannot alter production configuration or policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_version: str
    manifest_hash: str = Field(min_length=1, max_length=128)
    case_count: int = Field(ge=0)
    results: tuple[MultiAgentCaseResult, ...]
    baseline_metrics: dict[str, float | int | None]
    candidate_metrics: dict[str, float | int | None]
    gate_passed: bool
    gate_failures: tuple[str, ...] = ()
