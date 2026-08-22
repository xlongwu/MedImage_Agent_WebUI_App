from __future__ import annotations

import json
import logging

from src.backend.app.core.agent_logging import agent_log_context
from src.backend.app.core.logging_config import JsonLogFormatter


def test_agent_log_context_allows_only_bounded_correlation_identifiers() -> None:
    context = agent_log_context(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        reviewed_plan_id="plan-1",
        execution_ticket_id="ticket-1",
        run_id="run-1",
        sandbox_id="sandbox-1",
        event_code="AGENT_PLANNING_COMPLETED",
    )

    assert context == {
        "project_id": "project-1",
        "event_code": "AGENT_PLANNING_COMPLETED",
        "lifecycle_id": "lifecycle-1",
        "reviewed_plan_id": "plan-1",
        "execution_ticket_id": "ticket-1",
        "run_id": "run-1",
        "sandbox_id": "sandbox-1",
    }
    assert set(context).isdisjoint(
        {"goal", "prompt", "response", "path", "token", "memory", "patient_id"}
    )


def test_agent_log_context_drops_path_like_or_unbounded_values() -> None:
    context = agent_log_context(
        project_id=r"D:\research\patient-1",
        lifecycle_id="x" * 257,
        event_code="AGENT_EVENT",
    )

    assert context == {"event_code": "AGENT_EVENT"}
    record = logging.LogRecord(
        "src.backend.app.agent",
        logging.INFO,
        __file__,
        1,
        "agent_event",
        (),
        None,
    )
    record.medimage = context
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["event_code"] == "AGENT_EVENT"
    assert "research" not in json.dumps(payload)
