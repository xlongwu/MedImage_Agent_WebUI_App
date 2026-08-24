"""Read-only project library projection backed by canonical Agent Task state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.backend.app.schemas.desktop import ProjectAgentTaskSummary, ProjectSummary
from src.backend.app.services.agent_task_read_model import AgentTaskReadModel

if TYPE_CHECKING:
    from src.backend.app.api.dependencies import ProjectStore


class ProjectAgentSummaryService:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self.agent_tasks = AgentTaskReadModel(store)

    def list_projects(self) -> list[ProjectSummary]:
        projected: list[ProjectSummary] = []
        for project in self.store.list_projects():
            lifecycles = self.store.list_agent_lifecycles(project.id)
            latest = max(lifecycles, key=lambda item: item.updated_at, default=None)
            summary = None
            if latest is not None:
                task = self.agent_tasks.get(project_id=project.id, task_id=latest.lifecycle_id)
                summary = ProjectAgentTaskSummary(
                    task_id=task.task_id,
                    state=task.state,
                    outcome=task.outcome,
                    goal_summary=task.goal_summary,
                    current_action=task.current_action,
                    current_action_code=task.current_action_code,
                    requires_user=task.next_action.requires_user,
                    result_title=task.result_summary.title if task.result_summary else None,
                    recent_activity=task.current_action,
                    updated_at=task.updated_at.isoformat(),
                )
            projected.append(project.model_copy(update={"latest_agent_task": summary}))
        return projected
