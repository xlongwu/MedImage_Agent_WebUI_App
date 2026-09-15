"""Durable, bounded coordination of an already-dispatched execution run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from uuid import uuid4

from src.backend.app.schemas.agent_execution_wake import AgentExecutionWakeRecord


class AgentExecutionCoordinator:
    """Consume run-evidence checks without acquiring planning or execution power."""

    LEASE_SECONDS = 30
    POLL_INTERVAL_SECONDS = 1

    def __init__(self, store, *, reconciler, start_workers: bool = True, now=None) -> None:
        self.store = store
        self.reconciler = reconciler
        self.start_workers = start_workers
        self.now = now or (lambda: datetime.now(UTC))
        self._accepting = True
        self._lock = Lock()
        self._wake_event = Event()
        self._worker: Thread | None = None

    def schedule(self, *, lifecycle, delay_seconds: int = 0) -> AgentExecutionWakeRecord | None:
        """Schedule an evidence-only check for the lifecycle's existing run."""
        if not lifecycle.run_id or lifecycle.state not in {"RUNNING", "RETRYING", "RECOVERING"}:
            return None
        now = self.now()
        record = AgentExecutionWakeRecord(
            wake_id=f"agent_execution_wake_{uuid4().hex}",
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            run_id=lifecycle.run_id,
            available_at=now + timedelta(seconds=max(0, delay_seconds)),
            created_at=now,
            updated_at=now,
        )
        persisted = self.store.enqueue_agent_execution_wake(record)
        self._wake_event.set()
        if self._accepting and self.start_workers:
            self._start_worker()
        return persisted

    def notify_terminal_run(self, *, lifecycle) -> AgentExecutionWakeRecord | None:
        """Make a due check visible immediately after an observed terminal event."""
        return self.schedule(lifecycle=lifecycle, delay_seconds=0)

    def schedule_lifecycle(self, *, project_id: str, lifecycle_id: str) -> bool:
        lifecycle = self.reconciler.orchestrator.get(
            project_id=project_id, lifecycle_id=lifecycle_id
        )
        return self.schedule(lifecycle=lifecycle) is not None

    def run_once(self, *, owner: str | None = None):
        if not self._accepting:
            return None
        owner = owner or f"agent-execution-coordinator:{uuid4().hex}"
        now = self.now()
        record = self.store.claim_next_agent_execution_wake(
            owner=owner, now=now, lease_expires_at=now + timedelta(seconds=self.LEASE_SECONDS)
        )
        if record is None:
            return None
        try:
            lifecycle = self.reconciler.reconcile_once(
                project_id=record.project_id, lifecycle_id=record.lifecycle_id,
            )
        except Exception as exc:
            self.store.retry_agent_execution_wake(
                record, owner=owner, now=self.now(),
                available_at=self.now() + timedelta(seconds=self.POLL_INTERVAL_SECONDS),
                error_code=getattr(exc, "code", None) or type(exc).__name__,
            )
            raise
        if lifecycle.state in {"RUNNING", "RETRYING", "RECOVERING"} and lifecycle.run_id == record.run_id:
            self.store.retry_agent_execution_wake(
                record, owner=owner, now=self.now(),
                available_at=self.now() + timedelta(seconds=self.POLL_INTERVAL_SECONDS),
                error_code="AGENT_EXECUTION_EVIDENCE_PENDING",
            )
        else:
            self.store.complete_agent_execution_wake(record, owner=owner, now=self.now())
        return lifecycle

    def recover_on_startup(self) -> tuple[str, ...]:
        """Page every project/lifecycle; no first-batch cutoff can hide a run."""
        scheduled: list[str] = []
        for project in self.store.list_projects():
            for lifecycle in self.store.list_agent_lifecycles(project.id):
                if lifecycle.state in {"RUNNING", "RETRYING", "RECOVERING"}:
                    self.schedule(lifecycle=lifecycle)
                    scheduled.append(lifecycle.lifecycle_id)
        return tuple(scheduled)

    def shutdown(self) -> bool:
        self._accepting = False
        self._wake_event.set()
        with self._lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=self.LEASE_SECONDS)
        return worker is None or not worker.is_alive()

    def _start_worker(self) -> None:
        with self._lock:
            if not self._accepting or (self._worker is not None and self._worker.is_alive()):
                return
            self._worker = Thread(target=self._run_worker, name="agent-execution-coordinator", daemon=True)
            self._worker.start()

    def _run_worker(self) -> None:
        try:
            while self._accepting:
                if self.run_once() is not None:
                    continue
                self._wake_event.wait(timeout=self.POLL_INTERVAL_SECONDS)
                self._wake_event.clear()
        finally:
            with self._lock:
                self._worker = None
