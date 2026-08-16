"""Versioned, data-free fixtures for offline Agent regression evaluation."""

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


# Phase 13, stage 7 deliberately describes an offline comparison only. These
# models cannot carry production authority and are not persisted in a store.
AgentReviewerKind = Literal["science", "safety", "completeness"]
AgentReviewSeverity = Literal["warning", "blocking"]
EvaluationSourceKind = Literal["synthetic", "trace_replay_redacted"]
CandidateStatus = Literal["completed", "partial", "blocked", "handoff"]
EvaluationScenario = Literal[
    "plan_only",
    "missing_prerequisite",
    "alff_falff",
    "reho",
    "motion",
    "qc",
    "unsupported_goal",
    "environment_unavailable",
    "unsafe_write_root",
    "invalid_model_action",
]


class AgentReviewFinding(BaseModel):
    """Fixed, read-only reviewer output from the frozen Harness context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_kind: AgentReviewerKind
    severity: AgentReviewSeverity
    code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z0-9_]+$")
    message_key: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9_.-]+$")
    input_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    suggested_change: str | None = Field(default=None, max_length=512)


class RecordedReviewerRun(BaseModel):
    """A pre-recorded, tool-free reviewer response used by offline replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_kind: AgentReviewerKind
    status: Literal["completed", "timeout", "unavailable"] = "completed"
    findings: tuple[AgentReviewFinding, ...] = Field(default_factory=tuple, max_length=8)
    input_tokens: int = Field(ge=0, le=1_000_000)
    latency_ms: int = Field(ge=0, le=3_600_000)

    @model_validator(mode="after")
    def completed_runs_have_findings_only_for_their_role(self) -> RecordedReviewerRun:
        if any(finding.reviewer_kind != self.reviewer_kind for finding in self.findings):
            raise ValueError("MULTI_AGENT_EVAL_REVIEWER_KIND_MISMATCH")
        if self.status == "completed" and not self.findings:
            raise ValueError("MULTI_AGENT_EVAL_REVIEWER_FINDINGS_REQUIRED")
        if self.status != "completed" and self.findings:
            raise ValueError("MULTI_AGENT_EVAL_FAILED_REVIEWER_FINDINGS_FORBIDDEN")
        return self


class RecordedEvaluationRun(BaseModel):
    """Human-labelled output and cost facts for one comparison arm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    false_positive_blocking_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    repeated_conclusion_hashes: tuple[str, ...] = Field(min_length=2, max_length=8)
    call_count: int = Field(ge=0, le=32)
    input_tokens: int = Field(ge=0, le=1_000_000)
    latency_ms: int = Field(ge=0, le=3_600_000)
    human_operations: int = Field(ge=0, le=32)


class MultiAgentEvalCase(BaseModel):
    """One safe, frozen single-Agent versus read-only-review comparison case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=128)
    source_kind: EvaluationSourceKind
    source_ref_hash: str = Field(min_length=8, max_length=128)
    language: Literal["en", "zh-CN"]
    scenarios: tuple[EvaluationScenario, ...] = Field(min_length=1, max_length=8)
    frozen_context_hash: str = Field(min_length=8, max_length=128)
    input_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    reference_blocking_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    baseline: RecordedEvaluationRun
    reviewers: tuple[RecordedReviewerRun, ...] = Field(min_length=1, max_length=3)
    candidate: RecordedEvaluationRun

    @model_validator(mode="after")
    def frozen_case_invariants(self) -> MultiAgentEvalCase:
        if len({review.reviewer_kind for review in self.reviewers}) != len(self.reviewers):
            raise ValueError("MULTI_AGENT_EVAL_REVIEWER_DUPLICATE")
        if not set(self.reference_blocking_codes) <= set(self.baseline.finding_codes) | {
            finding.code for review in self.reviewers for finding in review.findings
        }:
            raise ValueError("MULTI_AGENT_EVAL_REFERENCE_FINDING_UNKNOWN")
        return self


class MultiAgentEvalManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    suite_version: str = Field(min_length=1, max_length=64)
    baseline_id: str = Field(min_length=1, max_length=128)
    cases: tuple[MultiAgentEvalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def corpus_covers_the_stage_seven_matrix(self) -> MultiAgentEvalManifest:
        required = {
            "plan_only", "missing_prerequisite", "alff_falff", "reho", "motion", "qc",
            "unsupported_goal", "environment_unavailable", "unsafe_write_root", "invalid_model_action",
        }
        observed = {scenario for case in self.cases for scenario in case.scenarios}
        if not required <= observed:
            raise ValueError("MULTI_AGENT_EVAL_REQUIRED_SCENARIO_MISSING")
        if {case.language for case in self.cases} != {"en", "zh-CN"}:
            raise ValueError("MULTI_AGENT_EVAL_LANGUAGE_COVERAGE_INVALID")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("MULTI_AGENT_EVAL_CASE_ID_DUPLICATE")
        return self


class MultiAgentCaseResult(BaseModel):
    """Deterministic evaluation result; never a plan, lifecycle, or ticket."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    candidate_status: CandidateStatus
    aggregated_findings: tuple[AgentReviewFinding, ...] = ()
    rejected_codes: tuple[str, ...] = ()
    baseline: RecordedEvaluationRun
    candidate: RecordedEvaluationRun
    candidate_call_count: int = Field(ge=0)
    candidate_input_tokens: int = Field(ge=0)
    candidate_latency_ms: int = Field(ge=0)


class MultiAgentEvaluationReport(BaseModel):
    """An offline evidence report. It cannot alter production policy or mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_version: str
    manifest_hash: str = Field(min_length=1, max_length=128)
    case_count: int = Field(ge=0)
    results: tuple[MultiAgentCaseResult, ...]
    baseline_metrics: dict[str, float | int | None]
    candidate_metrics: dict[str, float | int | None]
    gate_passed: bool
    gate_failures: tuple[str, ...] = ()
    conclusion: Literal["continue_single_agent", "production_design_requires_approval"]
