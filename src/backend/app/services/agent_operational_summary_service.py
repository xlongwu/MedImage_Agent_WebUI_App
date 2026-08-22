"""Read-only aggregation of persisted Agent operational records."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from src.backend.app.core.exceptions import NotFoundError, PipelineError
from src.backend.app.schemas.agent_operations import (
    AgentOperationalAttention,
    AgentOperationalSummary,
)
from src.backend.app.services.memory_retrieval_service import MemoryRetrievalService


_TERMINAL_STATES = {"GOAL_SATISFIED", "SUCCEEDED", "FAILED", "HUMAN_HANDOFF", "CANCELED"}
_GATEWAY_TERMINAL_EVENTS = {"dispatch_succeeded", "dispatch_failed", "dispatch_rejected"}


def _percentile(values: list[int], percentile: float) -> int | None:
    """Return a deterministic nearest-rank percentile without a numeric dependency."""

    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.5)))
    return ordered[index]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class AgentOperationalSummaryService:
    def __init__(self, store, *, memory_repository=None, memory_config=None) -> None:
        self.store = store
        self.memory_repository = memory_repository
        self.memory_config = memory_config

    def build(
        self, *, project_id: str, window_hours: int = 168, max_tasks: int = 500
    ) -> AgentOperationalSummary:
        if not 1 <= window_hours <= 720 or not 1 <= max_tasks <= 500:
            raise PipelineError("AGENT_OP_QUERY_INVALID", code="AGENT_OP_QUERY_INVALID")
        if self.store.get_project(project_id) is None:
            raise NotFoundError(
                "AGENT_OP_PROJECT_NOT_FOUND", code="AGENT_OP_PROJECT_NOT_FOUND"
            )

        generated_at = datetime.now(UTC)
        cutoff = generated_at - timedelta(hours=window_hours)
        lifecycles = [
            item
            for item in self.store.list_agent_lifecycles(project_id)
            if _aware(item.updated_at) >= cutoff
        ]
        lifecycles.sort(key=lambda item: _aware(item.updated_at), reverse=True)
        truncated = len(lifecycles) > max_tasks
        lifecycles = lifecycles[:max_tasks]
        lifecycle_ids = {item.lifecycle_id for item in lifecycles}
        lifecycle_by_id = {item.lifecycle_id: item for item in lifecycles}

        tasks = Counter(item.state for item in lifecycles)
        tasks["total"] = len(lifecycles)
        calls: Counter[str] = Counter()
        provider_failures: Counter[str] = Counter()
        scheduler: Counter[str] = Counter()
        approvals: Counter[str] = Counter()
        gateway: Counter[str] = Counter()
        sandbox: Counter[str] = Counter()
        attention: dict[tuple[str, str], list[str]] = defaultdict(list)
        call_latencies: list[int] = []
        task_latencies: list[int] = []

        approvals["waiting"] = tasks.get("WAITING_FOR_APPROVAL", 0)
        approvals["approved"] = tasks.get("APPROVED", 0) + tasks.get("EXECUTION_READY", 0)
        for lifecycle in lifecycles:
            task_latencies.append(
                int(
                    max(
                        0.0,
                        (_aware(lifecycle.updated_at) - _aware(lifecycle.created_at)).total_seconds()
                        * 1000,
                    )
                )
            )
            attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
            if attempt:
                for step in self.store.list_agent_harness_steps(attempt.attempt_id):
                    for call in step.model_calls:
                        calls["total"] += 1
                        if call.status in {"completed", "succeeded"}:
                            calls["success"] += 1
                        elif call.status == "failed":
                            calls["failure"] += 1
                            code = call.error_code or "UNKNOWN_PROVIDER_FAILURE"
                            provider_failures[code] += 1
                            attention[("AGENT_OP_PROVIDER_FAILURES", "warning")].append(
                                call.call_id
                            )
                        else:
                            calls["unknown"] += 1
                        if call.latency_ms is not None:
                            call_latencies.append(call.latency_ms)
                        if call.status == "started" and call.completed_at is None:
                            attention[("AGENT_OP_UNKNOWN_MODEL_CALL", "blocking")].append(
                                call.call_id
                            )
            for audit in self.store.list_agent_invariant_audits(lifecycle.lifecycle_id):
                if _aware(audit.created_at) >= cutoff and audit.blocking_count:
                    attention[("AGENT_OP_INVARIANT_BLOCKING", "blocking")].extend(
                        [audit.audit_id] * audit.blocking_count
                    )

        wakes = [
            wake
            for wake in self.store.list_agent_task_wakes(
                project_id=project_id, include_consumed=True
            )
            if wake.lifecycle_id in lifecycle_ids and _aware(wake.updated_at) >= cutoff
        ]
        for wake in wakes:
            scheduler[wake.status.lower()] += 1
            lifecycle = lifecycle_by_id.get(wake.lifecycle_id)
            if (
                wake.status == "CLAIMED"
                and wake.lease_expires_at
                and _aware(wake.lease_expires_at) < generated_at
                and lifecycle is not None
                and lifecycle.state not in _TERMINAL_STATES
            ):
                scheduler["overdue"] += 1
                attention[("AGENT_OP_WAKE_OVERDUE", "warning")].append(wake.wake_id)

        for ticket in self.store.list_execution_tickets(project_id):
            if _aware(ticket.issued_at) < cutoff:
                continue
            approvals[f"ticket_{ticket.status}"] += 1
            dispatch = self.store.get_gateway_dispatch_by_ticket(ticket.execution_ticket_id)
            if dispatch is None:
                continue
            events = [
                event
                for event in self.store.list_gateway_dispatch_events(dispatch.dispatch_id)
                if _aware(event.occurred_at) >= cutoff
            ]
            event_types = {event.event_type for event in events}
            for event in events:
                gateway[event.event_type] += 1
            if "dispatch_started" in event_types and not event_types.intersection(
                _GATEWAY_TERMINAL_EVENTS
            ):
                attention[("AGENT_OP_GATEWAY_OUTCOME_UNKNOWN", "blocking")].append(
                    dispatch.dispatch_id
                )
            for attempt in self.store.list_sandbox_attempts_for_run(
                project_id, dispatch.run_id
            ):
                sandbox[attempt.status.lower()] += 1
                if attempt.status == "INTERRUPTED":
                    attention[("AGENT_OP_SANDBOX_INTERRUPTED", "warning")].append(
                        attempt.sandbox_id
                    )

        memory_status, memory_unavailable = self._memory_status(project_id)
        if memory_unavailable:
            attention[("AGENT_OP_MEMORY_UNAVAILABLE", "warning")].append(project_id)

        return AgentOperationalSummary(
            project_id=project_id,
            window_started_at=cutoff,
            generated_at=generated_at,
            truncated=truncated,
            task_counts=dict(sorted(tasks.items())),
            model_call_counts=dict(sorted(calls.items())),
            provider_failure_counts=dict(sorted(provider_failures.items())),
            scheduler_counts=dict(sorted(scheduler.items())),
            approval_counts=dict(sorted(approvals.items())),
            gateway_counts=dict(sorted(gateway.items())),
            sandbox_counts=dict(sorted(sandbox.items())),
            memory_status=memory_status,
            latency_ms={
                "model_call_p50": _percentile(call_latencies, 0.50),
                "model_call_p95": _percentile(call_latencies, 0.95),
                "task_p50": _percentile(task_latencies, 0.50),
                "task_p95": _percentile(task_latencies, 0.95),
            },
            attention=tuple(
                AgentOperationalAttention(
                    code=code,
                    severity=severity,
                    count=len(ids),
                    related_ids=tuple(dict.fromkeys(ids))[:20],
                )
                for (code, severity), ids in sorted(attention.items())
                if ids
            ),
        )

    def _memory_status(self, project_id: str) -> tuple[str, bool]:
        consent = self.store.get_memory_consent(project_id)
        project_enabled = bool(consent.get("generate_enabled") or consent.get("use_enabled"))
        if self.memory_repository is None or self.memory_config is None:
            return ("unavailable", project_enabled)
        health: dict[str, Any] = MemoryRetrievalService(
            repository=self.memory_repository,
            project_store=self.store,
            config=self.memory_config,
        ).operational_health(project_id=project_id, consent=consent)
        status = str(health.get("status") or "failure")
        return status, project_enabled and status in {"disabled", "failure"}
