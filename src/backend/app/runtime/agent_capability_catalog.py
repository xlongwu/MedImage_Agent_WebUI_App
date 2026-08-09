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
_RESULT_CONTEXT = frozenset({
    "goal", "policy", "plan_state", "execution_state", "latest_observation",
    "last_action_result", "budget",
})
_RECOVERY_CONTEXT = frozenset({
    "goal", "policy", "project_evidence", "plan_state", "execution_state",
    "latest_observation", "last_action_result", "budget",
})
_ALL_CONTEXT = frozenset({
    "goal", "policy", "project_evidence", "decision_state", "plan_state",
    "execution_state", "latest_observation", "last_action_result", "memory_context", "budget",
})

AGENT_CAPABILITY_CATALOG: dict[AgentHarnessActionKind, AgentCapability] = {
    "read_evidence": AgentCapability(
        "read_evidence", "A0", _PLANNING_STATES, _ALL_CONTEXT,
        frozenset({"evidence_reference"}), False, "read_only",
    ),
    "request_decision": AgentCapability(
        "request_decision", "A1", _PLANNING_STATES, _PLANNING_CONTEXT,
        frozenset({"decision_request"}), False, "managed_state",
    ),
    "draft_plan": AgentCapability(
        "draft_plan", "A1", _PLANNING_STATES, _PLANNING_CONTEXT,
        frozenset({"reviewed_plan_request"}), False, "managed_state",
    ),
    "explain_result": AgentCapability(
        "explain_result", "A1",
        frozenset({"GOAL_SATISFIED", "SUCCEEDED", "HUMAN_HANDOFF", "CANCELED"}),
        _RESULT_CONTEXT, frozenset({"result_explanation"}), False, "managed_state",
    ),
    "propose_recovery": AgentCapability(
        "propose_recovery", "A1", frozenset({"DIAGNOSING"}), _RECOVERY_CONTEXT,
        frozenset({"recovery_proposal"}), False, "managed_state",
    ),
    "finish": AgentCapability(
        "finish", "A1",
        frozenset({
            "CREATED", "CONTEXT_READY", "PLAN_DRAFTED", "WAITING_FOR_INPUT",
            "WAITING_FOR_SCIENCE_DECISION", "WAITING_FOR_APPROVAL", "GOAL_SATISFIED",
            "SUCCEEDED", "HUMAN_HANDOFF", "CANCELED",
        }),
        frozenset({"goal", "policy", "last_action_result", "budget"}),
        frozenset({"attempt_finished"}), False, "managed_state",
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
