"""LLM Planner API — POST /api/planner/plan-from-goal."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.backend.app.planner.llm_planner import generate_plan_from_goal
from src.backend.app.services.goal_planning_service import GoalPlanningService

router = APIRouter()


class PlanFromGoalRequest(BaseModel):
    goal: str
    provider: str = "rule_based"
    project_id: str | None = None
    project_config_path: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


def _context_error_response(
    request: PlanFromGoalRequest,
    error: str,
) -> dict[str, Any]:
    return GoalPlanningService.context_error(
        goal=request.goal,
        provider=request.provider,
        error=error,
    )


@router.post("/api/planner/plan-from-goal")
def api_plan_from_goal(request: PlanFromGoalRequest) -> dict[str, Any]:
    """Generate a candidate pipeline plan from a natural-language goal.

    Returns PlannerResponse.to_dict().  Business errors (empty goal,
    unsupported goal, unsupported provider) are returned as HTTP 200
    with ok=false.  Only malformed request bodies trigger HTTP 422.
    """
    return GoalPlanningService(planner=generate_plan_from_goal).plan(
        goal=request.goal,
        provider=request.provider,
        project_id=request.project_id,
        project_config_path=request.project_config_path,
        constraints=request.constraints,
    )
