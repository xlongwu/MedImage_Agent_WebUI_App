"""Fail-closed legacy Agent execution route.

Planning and task lifecycle endpoints belong to ``agent_task_routes``.  This
route remains only to return the audited refusal required for the retired
``/api/agent/execute`` contract.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.backend.app.api.execution_contract import reject_execution_contract
from src.backend.app.api.models import AgentExecuteRequest

router = APIRouter()

@router.post("/api/agent/execute")
def agent_execute(request: AgentExecuteRequest) -> dict[str, Any]:
    reject_execution_contract("agent.execute")
