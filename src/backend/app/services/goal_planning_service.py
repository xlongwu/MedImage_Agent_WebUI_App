"""Shared application service for context-bound goal planning."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.backend.app.planner.llm_planner import generate_plan_from_goal
from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.planner.project_context import (
    ProjectContextError,
    apply_project_context_to_plan,
    load_project_context,
)


class GoalPlanningService:
    """Build a candidate plan without executing or persisting it."""

    def __init__(self, planner: Callable[..., Any] = generate_plan_from_goal) -> None:
        self.planner = planner

    def plan(
        self,
        *,
        goal: str,
        provider: str = "rule_based",
        project_id: str | None = None,
        project_config_path: str | None = None,
        constraints: dict[str, Any] | None = None,
        store=None,
    ) -> dict[str, Any]:
        if not project_id and not project_config_path:
            return self.context_error(
                goal=goal,
                provider=provider,
                error="PROJECT_CONTEXT_REQUIRED: select a project or provide an explicit project_config_path",
            )
        try:
            context = load_project_context(
                project_id=project_id,
                project_config_path=project_config_path,
                store=store,
            )
        except ProjectContextError as exc:
            return self.context_error(goal=goal, provider=provider, error=str(exc))

        planner_constraints = dict(constraints or {})
        planner_constraints.setdefault("project_context", context.to_dict())
        result = self.planner(
            goal=goal,
            provider=provider,
            constraints=planner_constraints,
            project_config_path=project_config_path,
        ).to_dict()
        result["project_context"] = context.to_dict()
        if not result.get("ok") or not isinstance(result.get("plan"), dict):
            return result
        try:
            plan = apply_project_context_to_plan(result["plan"], context)
        except ProjectContextError as exc:
            result.update(ok=False, plan={}, validation={})
            result["errors"] = [*result.get("errors", []), str(exc)]
            return result
        validation = validate_plan(plan).to_dict()
        result.update(plan=plan, validation=validation, ok=bool(validation.get("ok")))
        result["warnings"] = [
            *result.get("warnings", []),
            "Project context was applied before plan review.",
        ]
        return result

    @staticmethod
    def context_error(*, goal: str, provider: str, error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "provider": provider,
            "goal": goal,
            "plan": {},
            "validation": {},
            "messages": [],
            "warnings": [],
            "errors": [error],
            "project_context": None,
        }
