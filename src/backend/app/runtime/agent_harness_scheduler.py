"""Lifespan-owned, finite wake-up scheduler for persisted Harness attempts."""

from __future__ import annotations

from collections import deque
from threading import Lock, Thread
from typing import TYPE_CHECKING

from src.backend.app.core.config import ConfigService
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService

if TYPE_CHECKING:
    pass


_SCHEDULERS_GUARD = Lock()
_SCHEDULERS: dict[int, AgentHarnessScheduler] = {}


def get_agent_harness_scheduler(store, *, config=None) -> AgentHarnessScheduler:
    """Return the one in-process scheduler associated with this store object."""
    key = id(store)
    with _SCHEDULERS_GUARD:
        scheduler = _SCHEDULERS.get(key)
        if scheduler is None or scheduler.store is not store or not scheduler._accepting:
            scheduler = AgentHarnessScheduler(store, config=config)
            _SCHEDULERS[key] = scheduler
        return scheduler


class AgentHarnessScheduler:
    """Queue bounded wake-ups; the Harness remains the only step executor."""

    STARTUP_BATCH_LIMIT = 20
    PENDING_BATCH_LIMIT = 20

    def __init__(
        self,
        store,
        *,
        config=None,
        harness_service: AgentHarnessService | None = None,
        start_workers: bool = True,
    ) -> None:
        self.store = store
        self.config = config or ConfigService().harness
        self.harness_service = harness_service
        self.start_workers = start_workers
        self._lock = Lock()
        self._pending: deque[tuple[str, str, str, str, str | None]] = deque()
        self._pending_keys: set[str] = set()
        self._worker: Thread | None = None
        self._accepting = True

    def wake(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        reason: str,
        details: dict[str, str | None] | None = None,
    ) -> bool:
        """Idempotently record a wake-up and let the owner process it in background."""
        if not self.config.enabled or not self._accepting:
            return False
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        attempt = self.store.get_agent_harness_attempt(lifecycle_id)
        if lifecycle is None or attempt is None or lifecycle.project_id != project_id:
            return False
        fingerprint = stable_hash(details) if details is not None else None
        if fingerprint is not None and attempt.last_wake_fingerprint == fingerprint:
            return False
        wake_key = f"{lifecycle_id}:{fingerprint or lifecycle.updated_at.isoformat()}:{reason}"
        with self._lock:
            if wake_key in self._pending_keys:
                return False
            self._pending.append((project_id, lifecycle_id, reason[:128], wake_key, fingerprint))
            self._pending_keys.add(wake_key)
        if self.start_workers:
            self._start_worker()
        return True

    def run_pending_batch(self, *, batch_limit: int | None = None) -> tuple[str, ...]:
        """Process a finite number of lifecycle wake-ups in FIFO order."""
        if not self.config.enabled or not self._accepting:
            return ()
        limit = batch_limit or self.PENDING_BATCH_LIMIT
        pending = self._take_pending(limit)
        if not pending:
            return ()
        harness = self._harness()
        processed: list[str] = []
        for project_id, lifecycle_id, reason, _wake_key, fingerprint in pending:
            if not self._accepting:
                break
            lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
            if lifecycle is None or lifecycle.project_id != project_id:
                continue
            result = harness.run_until_blocked(
                lifecycle=lifecycle,
                actor="system-harness-scheduler",
                wake_reason=reason,
                wake_fingerprint=fingerprint,
                lease_owner=f"scheduler:{lifecycle_id}",
            )
            processed.append(lifecycle_id)
            if result.outcome == "yielded" and self._accepting:
                self._enqueue(
                    project_id=project_id,
                    lifecycle_id=lifecycle_id,
                    reason="fairness_yield",
                    dedupe_key=f"{lifecycle_id}:{result.attempt.next_step_no if result.attempt else 'none'}:fairness_yield",
                )
        return tuple(processed)

    def recover_once_on_startup(self) -> tuple[str, ...]:
        """Register recoverable attempts, then execute the normal batch path."""
        if not self.config.enabled or not self._accepting:
            return ()
        registered = 0
        for project in self.store.list_projects():
            for lifecycle in self.store.list_agent_lifecycles(project.id):
                if registered >= self.STARTUP_BATCH_LIMIT:
                    return self.run_pending_batch(batch_limit=self.STARTUP_BATCH_LIMIT)
                attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
                if attempt is None or attempt.status not in {"READY", "RUNNING"}:
                    continue
                if attempt.status == "RUNNING" and (
                    attempt.lease_expires_at is None or attempt.lease_expires_at > harness_now()
                ):
                    continue
                self._enqueue(
                    project_id=project.id,
                    lifecycle_id=lifecycle.lifecycle_id,
                    reason="startup_recovery",
                    dedupe_key=f"{lifecycle.lifecycle_id}:{attempt.next_step_no}:startup_recovery",
                )
                registered += 1
        return self.run_pending_batch(batch_limit=self.STARTUP_BATCH_LIMIT)

    def shutdown(self) -> bool:
        """Refuse new claims and wait only for the scheduler-owned current step."""
        self._accepting = False
        with self._lock:
            worker = self._worker
        if worker is None or not worker.is_alive():
            return True
        worker.join(timeout=min(float(self.config.lease_seconds), 30.0))
        return not worker.is_alive()

    def _harness(self) -> AgentHarnessService:
        command_service = AgentTaskCommandService(store=self.store, harness_scheduler=self)
        harness = self.harness_service or AgentHarnessService(
            self.store,
            config=self.config,
            draft_plan=lambda **kwargs: command_service._plan(**kwargs),
        )
        if harness.draft_plan is None:
            harness.draft_plan = lambda **kwargs: command_service._plan(**kwargs)
        return harness

    def _start_worker(self) -> None:
        with self._lock:
            if not self._accepting or (self._worker is not None and self._worker.is_alive()):
                return
            self._worker = Thread(
                target=self._run_worker,
                name="agent-harness-scheduler",
                daemon=True,
            )
            self._worker.start()

    def _run_worker(self) -> None:
        while self._accepting:
            if self.run_pending_batch():
                continue
            with self._lock:
                if self._pending and self._accepting:
                    continue
                self._worker = None
                return

    def _take_pending(self, limit: int) -> tuple[tuple[str, str, str, str, str | None], ...]:
        entries: list[tuple[str, str, str, str, str | None]] = []
        with self._lock:
            for _ in range(max(0, limit)):
                if not self._pending:
                    break
                item = self._pending.popleft()
                entries.append(item)
                self._pending_keys.discard(item[3])
        return tuple(entries)

    def _enqueue(self, *, project_id: str, lifecycle_id: str, reason: str, dedupe_key: str) -> bool:
        with self._lock:
            if dedupe_key in self._pending_keys:
                return False
            self._pending.append((project_id, lifecycle_id, reason[:128], dedupe_key, None))
            self._pending_keys.add(dedupe_key)
        return True


def harness_now():
    """Keep the startup expiration comparison timezone-aware and testable by patching one symbol."""
    from datetime import UTC, datetime

    return datetime.now(UTC)
