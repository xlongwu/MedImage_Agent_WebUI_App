"""Durable, bounded scheduler for persisted Agent planning work."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock, Thread
from typing import Callable, Protocol
from uuid import uuid4

from src.backend.app.schemas.agent_task_wake import AgentTaskWakeRecord


class AgentPlanningAdvancer(Protocol):
    def advance_planning(self, *, project_id: str, lifecycle_id: str, wake_reason: str) -> object: ...


class AgentTaskScheduler:
    """Consume only durable planning wakes.

    It intentionally has no execution, ticket, gateway, runner, or recovery
    dependencies.  The outbox is the authority; the optional worker merely
    reduces latency after a committed command.
    """

    LEASE_SECONDS = 30
    MAX_RETRY_DELAY_SECONDS = 30
    RESCAN_LIMIT = 100

    def __init__(
        self,
        store,
        *,
        planning_service: AgentPlanningAdvancer,
        start_workers: bool = True,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.planning_service = planning_service
        self.start_workers = start_workers
        self.now = now or (lambda: datetime.now(UTC))
        self._accepting = True
        self._lock = Lock()
        self._worker: Thread | None = None

    def enqueue(self, *, project_id: str, lifecycle_id: str, step_key: str, reason: str) -> AgentTaskWakeRecord:
        now = self.now()
        record = AgentTaskWakeRecord(
            wake_id=f"agent_wake_{uuid4().hex}", project_id=project_id,
            lifecycle_id=lifecycle_id, step_key=step_key, reason=reason,
            available_at=now, created_at=now, updated_at=now,
        )
        persisted = self.store.enqueue_agent_task_wake(record)
        if self._accepting and self.start_workers:
            self._start_worker()
        return persisted

    def notify(self) -> None:
        """Accelerate consumption of an already committed outbox record."""
        if self._accepting and self.start_workers:
            self._start_worker()

    def claim_next(self, *, owner: str) -> AgentTaskWakeRecord | None:
        now = self.now()
        return self.store.claim_next_agent_task_wake(
            owner=owner, now=now, lease_expires_at=now + timedelta(seconds=self.LEASE_SECONDS)
        )

    def run_once(self, *, owner: str | None = None) -> str | None:
        """Claim and advance exactly one persisted planning checkpoint."""
        if not self._accepting:
            return None
        owner = owner or f"agent-task-scheduler:{uuid4().hex}"
        wake = self.claim_next(owner=owner)
        if wake is None:
            return None
        now = self.now()
        try:
            self.planning_service.advance_planning(
                project_id=wake.project_id,
                lifecycle_id=wake.lifecycle_id,
                wake_reason=wake.reason,
            )
        except Exception as exc:
            # A failed bounded step remains durable and is retried with a
            # capped backoff.  Permanent domain errors must be converted by
            # AgentPlanningService into lifecycle HUMAN_HANDOFF rather than
            # being hidden as an endlessly retried scheduler error.
            delay = min(2 ** min(wake.attempts, 5), self.MAX_RETRY_DELAY_SECONDS)
            self.store.retry_agent_task_wake(
                wake, owner=owner, now=now,
                available_at=now + timedelta(seconds=delay),
                error_code=getattr(exc, "code", None) or type(exc).__name__,
            )
            return wake.lifecycle_id
        self.store.complete_agent_task_wake(wake, owner=owner, now=self.now())
        return wake.lifecycle_id

    def rescan(self) -> tuple[str, ...]:
        """Recreate acceleration hints only from persisted nonterminal state."""
        scheduled: list[str] = []
        ready = {"CREATED", "CONTEXT_READY", "PLAN_DRAFTED", "PLAN_VALIDATED"}
        for project in self.store.list_projects()[:self.RESCAN_LIMIT]:
            for lifecycle in self.store.list_agent_lifecycles(project.id):
                if lifecycle.state not in ready:
                    continue
                key = f"{lifecycle.state}:{lifecycle.updated_at.isoformat()}"
                existing = [
                    wake
                    for wake in self.store.list_agent_task_wakes(
                        project_id=project.id, include_consumed=True,
                    )
                    if wake.lifecycle_id == lifecycle.lifecycle_id and wake.step_key == key
                ]
                # Every non-consumed row is already a durable continuation:
                # pending/retry rows wait for their due time; claimed rows are
                # either actively leased or directly reclaimable after expiry.
                if any(wake.status != "CONSUMED" for wake in existing):
                    continue
                self.enqueue(
                    project_id=project.id, lifecycle_id=lifecycle.lifecycle_id,
                    step_key=key, reason="persistent_rescan",
                )
                scheduled.append(lifecycle.lifecycle_id)
        return tuple(scheduled)

    def recover_once_on_startup(self) -> tuple[str, ...]:
        self.rescan()
        processed: list[str] = []
        for _ in range(self.RESCAN_LIMIT):
            lifecycle_id = self.run_once(owner="agent-task-startup")
            if lifecycle_id is None:
                break
            processed.append(lifecycle_id)
        return tuple(processed)

    def shutdown(self) -> bool:
        self._accepting = False
        with self._lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=self.LEASE_SECONDS)
        return worker is None or not worker.is_alive()

    def _start_worker(self) -> None:
        with self._lock:
            if not self._accepting or (self._worker is not None and self._worker.is_alive()):
                return
            self._worker = Thread(target=self._run_worker, name="agent-task-scheduler", daemon=True)
            self._worker.start()

    def _run_worker(self) -> None:
        while self._accepting:
            if self.run_once() is None:
                break
        with self._lock:
            self._worker = None
