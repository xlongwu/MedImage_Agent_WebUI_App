"""Fail-closed A0/A1 policy for optional Agent Harness actions."""

from __future__ import annotations

from dataclasses import dataclass

from src.backend.app.schemas.agent_harness import AgentHarnessActionKind


@dataclass(frozen=True)
class AgentCapability:
    kind: AgentHarnessActionKind
    automation_level: str
    allowed_states: frozenset[str]
    allowed_context_sections: frozenset[str]
    allowed_output_types: frozenset[str]
    requires_current_approval: bool
    side_effect_class: str


_PLANNING_STATES = frozenset({"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"})

_PLANNING_CONTEXT = frozenset({
    "goal", "policy", "project_evidence", "decision_state", "plan_state",
    "last_action_result", "memory_context", "budget",
})
AGENT_CAPABILITY_CATALOG: dict[AgentHarnessActionKind, AgentCapability] = {
    "request_decision": AgentCapability(
        "request_decision", "A1", _PLANNING_STATES, _PLANNING_CONTEXT,
        frozenset({"decision_request"}), False, "managed_state",
    ),
    "draft_plan": AgentCapability(
        "draft_plan", "A1", _PLANNING_STATES, _PLANNING_CONTEXT,
        frozenset({"reviewed_plan_request"}), False, "managed_state",
    ),
}


def assert_capability_allowed(kind: AgentHarnessActionKind, lifecycle_state: str) -> AgentCapability:
    capability = AGENT_CAPABILITY_CATALOG.get(kind)
    if (
        capability is None
        or lifecycle_state not in capability.allowed_states
        or capability.automation_level not in {"A0", "A1"}
        or capability.requires_current_approval
        or capability.side_effect_class not in {"read_only", "managed_state"}
        or not capability.allowed_context_sections
        or not capability.allowed_output_types
    ):
        raise ValueError("AGENT_HARNESS_CAPABILITY_DENIED")
    return capability


def assert_capability_context_and_output_allowed(
    capability: AgentCapability,
    *,
    context_sections: set[str],
    output_type: str,
) -> None:
    """Reject an action that asks for undeclared context or output authority."""
    if (
        not context_sections.issubset(capability.allowed_context_sections)
        or output_type not in capability.allowed_output_types
    ):
        raise ValueError("AGENT_HARNESS_CAPABILITY_DENIED")
