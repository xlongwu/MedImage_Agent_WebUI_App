"""Versioned, data-free fixtures and metrics for offline Agent regression evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
