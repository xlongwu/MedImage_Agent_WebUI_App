"""Fail-closed allowlist for optional Agent Harness actions."""

from __future__ import annotations

from dataclasses import dataclass

from src.backend.app.schemas.agent_harness import AgentHarnessActionKind


@dataclass(frozen=True)
class AgentCapability:
    kind: AgentHarnessActionKind
    allowed_states: frozenset[str]
    read_only: bool


_PLANNING_STATES = frozenset({"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"})

AGENT_CAPABILITY_CATALOG: dict[AgentHarnessActionKind, AgentCapability] = {
    "read_evidence": AgentCapability("read_evidence", _PLANNING_STATES, True),
    "request_decision": AgentCapability("request_decision", _PLANNING_STATES, True),
    "draft_plan": AgentCapability("draft_plan", _PLANNING_STATES, True),
    "explain_result": AgentCapability(
        "explain_result",
        frozenset({"GOAL_SATISFIED", "SUCCEEDED", "HUMAN_HANDOFF", "CANCELED"}),
        True,
    ),
    "propose_recovery": AgentCapability("propose_recovery", frozenset({"DIAGNOSING"}), True),
    "finish": AgentCapability(
        "finish",
        frozenset({
            "CREATED", "CONTEXT_READY", "PLAN_DRAFTED", "WAITING_FOR_INPUT",
            "WAITING_FOR_SCIENCE_DECISION", "WAITING_FOR_APPROVAL", "GOAL_SATISFIED",
            "SUCCEEDED", "HUMAN_HANDOFF", "CANCELED",
        }),
        True,
    ),
}


def assert_capability_allowed(kind: AgentHarnessActionKind, lifecycle_state: str) -> AgentCapability:
    capability = AGENT_CAPABILITY_CATALOG.get(kind)
    if capability is None or lifecycle_state not in capability.allowed_states:
        raise ValueError("AGENT_HARNESS_CAPABILITY_DENIED")
    return capability
