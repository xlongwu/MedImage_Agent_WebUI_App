"""Bounded, versioned input contract for every Agent planning attempt."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RevisionReason = Literal[
    "initial",
    "decision_answered",
    "goal_revised",
    "recovery_replan",
]


class PlanningRequest(BaseModel):
    """All planner inputs frozen before candidate-plan generation.

    This contract deliberately contains only already-bound, structured values.
    Planner implementations must not reach into a memory database, rawdata, or
    frontend-local state to supplement it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    project_id: str
    lifecycle_id: str
    goal: str
    project_config_path: str
    evidence_snapshot_hash: str
    science_answers: dict[str, str] = Field(default_factory=dict)
    memory_context_hash: str | None = None
    memory_context_refs: tuple[dict[str, Any], ...] = ()
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: RevisionReason
    recovery_proposal_hash: str | None = None
    recovery_candidate_hash: str | None = None
    provider_ref: str
    prompt_version: str
    model_profile_hash: str = Field(min_length=64, max_length=64)

    def identity_payload(self) -> dict[str, Any]:
        """Return the exact data that can affect a reviewed-plan identity."""
        return self.model_dump(mode="json")

    def planner_constraints(self) -> dict[str, Any]:
        """Expose only confirmed decision answers to the provider adapter."""
        return {"science_answers": dict(self.science_answers)}
