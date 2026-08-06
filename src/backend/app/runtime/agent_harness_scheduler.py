"""Lifespan-owned, finite recovery pass for persisted Harness attempts."""

from __future__ import annotations

from src.backend.app.core.config import ConfigService
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService


class AgentHarnessScheduler:
    STARTUP_BATCH_LIMIT = 20

    def __init__(self, store, *, config=None) -> None:
        self.store = store
        self.config = config or ConfigService().harness

    def recover_once_on_startup(self) -> tuple[str, ...]:
        """Claim no more than one pending step per lifecycle and then return."""
        if not self.config.enabled:
            return ()
        processed: list[str] = []
        command_service = AgentTaskCommandService(store=self.store)
        harness = AgentHarnessService(
            self.store,
            config=self.config,
            draft_plan=lambda **kwargs: command_service._plan(**kwargs),
        )
        for project in self.store.list_projects():
            for lifecycle in self.store.list_agent_lifecycles(project.id):
                if len(processed) >= self.STARTUP_BATCH_LIMIT:
                    return tuple(processed)
                attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
                if attempt is None or attempt.status not in {"READY", "RUNNING"}:
                    continue
                result = harness.run_one(
                    lifecycle=lifecycle,
                    actor="system-harness-scheduler",
                    lease_owner=f"startup:{lifecycle.lifecycle_id}",
                )
                if result.attempt is not None:
                    processed.append(lifecycle.lifecycle_id)
        return tuple(processed)
