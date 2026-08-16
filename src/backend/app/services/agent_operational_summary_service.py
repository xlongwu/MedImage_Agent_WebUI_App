"""Read-only aggregation of persisted Agent operational records."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from src.backend.app.schemas.agent_operations import (
    AgentOperationalAttention,
    AgentOperationalSummary,
)


class AgentOperationalSummaryService:
    def __init__(self, store) -> None:
        self.store = store

    def build(self, *, project_id: str, window_hours: int = 168, max_tasks: int = 500) -> AgentOperationalSummary:
        if not 1 <= window_hours <= 720 or not 1 <= max_tasks <= 500:
            raise ValueError("AGENT_OP_QUERY_INVALID")
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        lifecycles = [item for item in self.store.list_agent_lifecycles(project_id) if item.updated_at >= cutoff]
        lifecycles.sort(key=lambda item: item.updated_at, reverse=True)
        truncated = len(lifecycles) > max_tasks
        lifecycles = lifecycles[:max_tasks]
        states = Counter(item.state for item in lifecycles)
        calls = Counter()
        attention = Counter()
        for lifecycle in lifecycles:
            attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
            if attempt:
                for step in self.store.list_agent_harness_steps(attempt.attempt_id):
                    for call in step.model_calls:
                        calls["total"] += 1
                        calls["success" if call.status == "completed" else "failure" if call.status == "failed" else "unknown"] += 1
                        if call.status == "started" and call.completed_at is None:
                            attention[("AGENT_OP_UNKNOWN_MODEL_CALL", "blocking")] += 1
                        if call.status == "failed":
                            attention[("AGENT_OP_PROVIDER_FAILURES", "warning")] += 1
            for audit in self.store.list_agent_invariant_audits(lifecycle.lifecycle_id):
                if audit.blocking_count:
                    attention[("AGENT_OP_INVARIANT_BLOCKING", "blocking")] += audit.blocking_count
            for wake in self.store.list_agent_task_wakes(project_id=project_id, include_consumed=False):
                if wake.lifecycle_id == lifecycle.lifecycle_id and wake.lease_expires_at and wake.lease_expires_at < datetime.now(UTC):
                    attention[("AGENT_OP_WAKE_OVERDUE", "warning")] += 1
        for ticket in self.store.list_execution_tickets(project_id):
            dispatch = self.store.get_gateway_dispatch_by_ticket(ticket.execution_ticket_id) if hasattr(self.store, "get_gateway_dispatch_by_ticket") else None
            if dispatch:
                events = self.store.list_gateway_dispatch_events(dispatch.dispatch_id)
                if any(event.event_type == "dispatch_started" for event in events) and not any(event.event_type in {"dispatch_succeeded", "dispatch_failed", "dispatch_rejected"} for event in events):
                    attention[("AGENT_OP_GATEWAY_OUTCOME_UNKNOWN", "blocking")] += 1
                for sandbox in self.store.list_sandbox_attempts_for_run(project_id, dispatch.run_id):
                    if sandbox.status == "INTERRUPTED":
                        attention[("AGENT_OP_SANDBOX_INTERRUPTED", "warning")] += 1
        return AgentOperationalSummary(
            project_id=project_id, window_hours=window_hours,
            lifecycle_state_counts=dict(sorted(states.items())), model_call_counts=dict(sorted(calls.items())),
            approval_waiting_count=states.get("WAITING_FOR_APPROVAL", 0),
            attentions=tuple(AgentOperationalAttention(code=code, severity=severity, count=count) for (code, severity), count in sorted(attention.items())),
            truncated=truncated,
        )
