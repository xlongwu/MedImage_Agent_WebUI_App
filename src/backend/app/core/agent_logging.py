"""Safe correlation fields for Agent control-plane logs."""

from __future__ import annotations

import re


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@-]{1,256}$")


def agent_log_context(
    *,
    project_id: str,
    event_code: str,
    lifecycle_id: str | None = None,
    reviewed_plan_id: str | None = None,
    execution_ticket_id: str | None = None,
    run_id: str | None = None,
    sandbox_id: str | None = None,
) -> dict[str, str]:
    """Return identifiers only; callers must never add user content or paths."""

    values = {
        "project_id": project_id,
        "event_code": event_code,
        "lifecycle_id": lifecycle_id,
        "reviewed_plan_id": reviewed_plan_id,
        "execution_ticket_id": execution_ticket_id,
        "run_id": run_id,
        "sandbox_id": sandbox_id,
    }
    return {
        key: value
        for key, value in values.items()
        if value and _SAFE_IDENTIFIER.fullmatch(value)
    }
