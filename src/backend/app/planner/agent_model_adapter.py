"""Provider-neutral ActionEnvelope adapter for the bounded Agent Harness."""

from __future__ import annotations

import json
import os
from typing import Protocol

from src.backend.app.schemas.agent_harness import ActionEnvelope


CONTEXT_V2_SECTION_ORDER = (
    "goal", "policy", "project_evidence", "decision_state", "plan_state",
    "execution_state", "latest_observation", "last_action_result", "memory_context", "budget",
)


def _canonical_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def serialize_context_v2(snapshot: dict) -> dict:
    """Validate and serialize Context v2 in a provider-cache-stable order.

    This is intentionally the sole adapter boundary.  It accepts no legacy
    flat snapshot, so a stored v1 row cannot silently reach a model provider.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 2:
        raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
    sections = snapshot.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(CONTEXT_V2_SECTION_ORDER):
        raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
    fixed_sections: dict[str, object] = {}
    for name in CONTEXT_V2_SECTION_ORDER:
        section = sections[name]
        if not isinstance(section, dict) or section.get("schema_version") != 1:
            raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
        fixed_sections[name] = _canonical_value(section)
    return {
        "schema_version": 2,
        "policy_version": str(snapshot.get("policy_version") or ""),
        "redaction_policy_version": str(snapshot.get("redaction_policy_version") or ""),
        "prompt_template_version": str(snapshot.get("prompt_template_version") or ""),
        "skill_refs": sorted(str(item) for item in snapshot.get("skill_refs", []) if isinstance(item, str)),
        "sections": fixed_sections,
        "omitted_fields": sorted(str(item) for item in snapshot.get("omitted_fields", []) if isinstance(item, str)),
    }


class AgentModelAdapter(Protocol):
    def propose_action(self, *, snapshot: dict, provider_ref: str, repair: bool = False) -> ActionEnvelope: ...


class DefaultAgentModelAdapter:
    """Small adapter for an explicitly selected Harness provider.

    The rule-based provider selects the only productive initial action
    deterministically. An OpenAI-compatible provider must be explicitly
    configured; provider failure stops the enabled Harness.
    """

    def propose_action(self, *, snapshot: dict, provider_ref: str, repair: bool = False) -> ActionEnvelope:
        snapshot = serialize_context_v2(snapshot)
        sections = snapshot["sections"]
        goal = sections.get("goal") if isinstance(sections, dict) else None
        goal_data = goal.get("data") if isinstance(goal, dict) else None
        state = str(goal_data.get("lifecycle_state") or "") if isinstance(goal_data, dict) else ""
        provider = provider_ref.strip().casefold()
        if provider == "rule_based":
            return ActionEnvelope(
                kind="draft_plan" if state in {"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"} else "finish",
                reason="Use the existing deterministic reviewed-planning service.",
                expected_state=state,
            )
        if provider != "openai_compatible" or not os.environ.get("MEDIMAGE_LLM_API_KEY"):
            raise RuntimeError("AGENT_HARNESS_PROVIDER_UNAVAILABLE")
        from src.backend.app.planner.llm_provider import call_openai_compatible_action_provider

        result = call_openai_compatible_action_provider(snapshot=snapshot, repair=repair)
        if not result.ok:
            raise RuntimeError(result.errors[0] if result.errors else "AGENT_HARNESS_MODEL_FAILED")
        return ActionEnvelope.model_validate_json(result.content)


def action_schema() -> dict[str, object]:
    """Expose the exact JSON Schema without passing executable tool metadata."""
    return ActionEnvelope.model_json_schema()


def build_action_prompt(snapshot: dict, *, repair: bool) -> str:
    repair_instruction = (
        "Your previous reply was invalid. Return only one corrected JSON object matching the schema."
        if repair
        else "Return only one JSON object matching the schema."
    )
    return (
        "You are an advice-only research planning assistant. You may not approve, execute, "
        "write files, invoke tools, or issue commands. "
        + repair_instruction
        + "\nACTION_ENVELOPE_SCHEMA:\n"
        + json.dumps(action_schema(), ensure_ascii=False, separators=(",", ":"))
        + "\nSAFE_CONTEXT:\n"
        + json.dumps(serialize_context_v2(snapshot), ensure_ascii=False, separators=(",", ":"))
    )
