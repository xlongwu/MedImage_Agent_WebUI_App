"""LLM Planner API — POST /api/planner/plan-from-goal."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.llm_planner import generate_plan_from_goal
from src.backend.app.schemas.planning import PlanningRequest
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
    if not request.project_id and not request.project_config_path:
        return _context_error_response(
            request,
            "PROJECT_CONTEXT_REQUIRED: select a project and provide its project_config_path",
        )
    planning_request = PlanningRequest(
        # This advisory endpoint also supports an explicit, read-only example
        # configuration before a desktop project exists.  Keep that state
        # unbound (rather than inventing a project ID); reviewed-plan storage
        # continues to require a real project ID before anything is persisted
        # or executed.
        project_id=request.project_id or "",
        lifecycle_id="advisory-plan-from-goal",
        goal=request.goal,
        project_config_path=request.project_config_path or "",
        evidence_snapshot_hash=stable_hash(
            {"project_id": request.project_id, "goal": request.goal, "constraints": request.constraints}
        ),
        science_answers={str(key): str(value) for key, value in request.constraints.items()},
        revision_reason="initial",
        provider_ref=request.provider,
        prompt_version="api-plan-from-goal-v1",
    )
    return GoalPlanningService(planner=generate_plan_from_goal).plan(request=planning_request)
