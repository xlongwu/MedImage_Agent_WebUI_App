"""Bounded, deterministic post-run lifecycle reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import monotonic

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.services.agent_orchestrator import AgentOrchestrator


@dataclass(frozen=True)
class TerminalRunEvidence:
    terminal: bool
    status: str
    complete: bool
    conflicting: bool = False


_LOCKS_GUARD = Lock()
_LOCKS: dict[str, Lock] = {}
_MONITORS_GUARD = Lock()
_MONITORS: set[str] = set()


class AgentTaskReconciler:
    MAX_TRANSITIONS = 4
    STARTUP_BATCH_LIMIT = 20
    STARTUP_WALL_SECONDS = 2.0
    MONITOR_MAX_CHECKS = 900
    MONITOR_INTERVAL_SECONDS = 1.0
    MONITOR_WALL_SECONDS = 900.0

    def __init__(self, store, *, harness_waker=None) -> None:
        self.store = store
        self.orchestrator = AgentOrchestrator(store)
        self.harness_waker = harness_waker

    def reconcile_once(self, *, project_id: str, lifecycle_id: str, actor: str = "system-reconciler"):
        lock = self._lock(lifecycle_id)
        with lock:
            current = self.orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
            if current.state != "RUNNING":
                return current
            evidence = self._terminal_evidence(current)
            if not evidence.terminal:
                return current
            base = f"reconcile:{current.lifecycle_id}:{current.run_id}"
            if evidence.conflicting or not evidence.complete:
                handed_off = self.orchestrator.transition(
                    project_id=project_id,
                    lifecycle_id=lifecycle_id,
                    to_state="HUMAN_HANDOFF",
                    command_id=f"{base}:handoff",
                    actor=actor,
                    source_command="terminal_evidence_incomplete",
                    reason=f"Terminal evidence is {evidence.status.lower()} or conflicting.",
                )
                self._wake_harness(handed_off, reason="run_terminal")
                return handed_off
            try:
                observed = self.orchestrator.observe(
                    project_id=project_id,
                    lifecycle_id=lifecycle_id,
                    command_id=f"{base}:observe",
                    actor=actor,
                )
                evaluated, evaluation = self.orchestrator.evaluate_goal(
                    project_id=project_id,
                    lifecycle_id=lifecycle_id,
                    command_id=f"{base}:evaluate:{observed.observation_id}",
                    actor=actor,
                )
                if evaluated.state == "DIAGNOSING" and evaluation.status == "not_satisfied":
                    evaluated, _diagnosis, _proposal = self.orchestrator.propose_recovery(
                        project_id=project_id,
                        lifecycle_id=lifecycle_id,
                        command_id=f"{base}:recovery:{evaluation.goal_evaluation_hash}",
                        actor=actor,
                    )
                if evaluated.state != "RUNNING":
                    self._wake_harness(evaluated, reason="run_terminal")
                return evaluated
            except (SafetyError, StateStoreError):
                latest = self.orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
                if latest.state != "RUNNING":
                    return latest
                raise

    def reconcile_incomplete_on_startup(self) -> tuple[str, ...]:
        started = monotonic()
        processed: list[str] = []
        for project in self.store.list_projects():
            for lifecycle in self.store.list_agent_lifecycles(project.id):
                if len(processed) >= self.STARTUP_BATCH_LIMIT or monotonic() - started >= self.STARTUP_WALL_SECONDS:
                    return tuple(processed)
                if lifecycle.state != "RUNNING":
                    continue
                current = self.reconcile_once(
                    project_id=project.id,
                    lifecycle_id=lifecycle.lifecycle_id,
                )
                if current.state == "RUNNING":
                    self.start_bounded_monitor(
                        project_id=project.id,
                        lifecycle_id=lifecycle.lifecycle_id,
                    )
                processed.append(lifecycle.lifecycle_id)
        return tuple(processed)

    def start_bounded_monitor(self, *, project_id: str, lifecycle_id: str) -> bool:
        """Start at most one finite terminal-evidence monitor for a lifecycle."""
        with _MONITORS_GUARD:
            if lifecycle_id in _MONITORS:
                return False
            _MONITORS.add(lifecycle_id)

        def run() -> None:
            try:
                self.monitor_bounded(project_id=project_id, lifecycle_id=lifecycle_id)
            finally:
                with _MONITORS_GUARD:
                    _MONITORS.discard(lifecycle_id)

        Thread(
            target=run,
            name=f"agent-task-reconcile-{lifecycle_id}",
            daemon=True,
        ).start()
        return True

    def monitor_bounded(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        max_checks: int | None = None,
        interval_seconds: float | None = None,
        waiter=None,
    ):
        """Poll terminal evidence within fixed count and wall-time bounds."""
        checks = max_checks if max_checks is not None else self.MONITOR_MAX_CHECKS
        interval = (
            interval_seconds
            if interval_seconds is not None
            else self.MONITOR_INTERVAL_SECONDS
        )
        wait = waiter or Event().wait
        started = monotonic()
        current = self.orchestrator.get(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
        )
        for _ in range(max(0, checks)):
            current = self.reconcile_once(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
            )
            if current.state != "RUNNING":
                return current
            elapsed = monotonic() - started
            if elapsed >= self.MONITOR_WALL_SECONDS:
                break
            wait(min(max(0.0, interval), self.MONITOR_WALL_SECONDS - elapsed))
        return current

    def _terminal_evidence(self, lifecycle) -> TerminalRunEvidence:
        if not lifecycle.run_id or not lifecycle.execution_ticket_id:
            return TerminalRunEvidence(terminal=True, status="INCOMPLETE", complete=False)
        link = self.store.get_run_link_by_run_id(lifecycle.project_id, lifecycle.run_id)
        if link is None:
            return TerminalRunEvidence(terminal=False, status="MISSING", complete=False)
        status = str(link.status).upper()
        terminal_statuses = {
            "SUCCESS", "SUCCEEDED", "COMPLETED", "FAILED", "ERROR",
            "PARTIAL", "INTERRUPTED", "CANCELLED",
        }
        terminal = status in terminal_statuses
        payload = link.payload if isinstance(link.payload, dict) else {}
        executor_result = payload.get("executor_result") if isinstance(payload.get("executor_result"), dict) else {}
        if not terminal and (
            executor_result.get("run_dir")
            or str(executor_result.get("status") or "").lower() in {"queued", "running", "cancel_requested"}
        ):
            project = self.store.get_project(lifecycle.project_id)
            metadata = project.metadata if project is not None and isinstance(project.metadata, dict) else {}
            project_dir = str(metadata.get("project_dir") or "")
            from src.backend.app.services.native_preproc_full import get_native_full_progress

            progress = get_native_full_progress(
                lifecycle.project_id,
                lifecycle.run_id,
                project_dir=project_dir,
            )
            progress_status = str(progress.get("status") or "UNKNOWN").upper()
            if progress_status in terminal_statuses:
                status = progress_status
                terminal = True
                payload = {**payload, "terminal_evidence_incomplete": progress.get("available") is not True}
        conflicting = bool(payload.get("terminal_conflict"))
        complete = bool(terminal and not payload.get("terminal_evidence_incomplete") and not conflicting)
        return TerminalRunEvidence(terminal=terminal, status=status, complete=complete, conflicting=conflicting)

    @staticmethod
    def _lock(lifecycle_id: str) -> Lock:
        with _LOCKS_GUARD:
            return _LOCKS.setdefault(lifecycle_id, Lock())

    def _wake_harness(self, lifecycle, *, reason: str) -> bool:
        """Wake only from the reconciler owner after terminal state persistence."""
        if self.harness_waker is not None:
            return bool(self.harness_waker(lifecycle=lifecycle, reason=reason))
        from src.backend.app.runtime.agent_harness_scheduler import get_agent_harness_scheduler

        scheduler = get_agent_harness_scheduler(self.store)
        return scheduler.wake(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            reason=reason,
        )
