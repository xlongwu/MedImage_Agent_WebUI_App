"""Canonical typed plan shared by rule-based and remote planners."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlannerPlanNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    depends_on: tuple[str, ...]
    params: dict[str, Any]
    name: str | None = None
    agent: str | None = None
    contract_version: str | None = None
    inputs: tuple[str, ...] | None = None
    outputs: tuple[str, ...] | None = None
    input_types: tuple[str, ...] | None = None
    output_types: tuple[str, ...] | None = None
    parallel_level: str | None = None
    gpu_supported: bool | None = None
    cache: bool | None = None


class PlannerPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline_id: str = Field(min_length=1)
    nodes: tuple[PlannerPlanNode, ...]
    version: str | None = None
    modality: str | None = None
    description: str | None = None
    execution: dict[str, Any] | None = None
    goal: str | None = None
    project_context: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    missing_prerequisites: tuple[str, ...] | None = None
    risks: tuple[str, ...] | None = None


def canonical_plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate and return one JSON-compatible canonical plan representation."""
    return PlannerPlan.model_validate(plan).model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
    )
