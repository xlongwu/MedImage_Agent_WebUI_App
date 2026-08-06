"""Provider-neutral ActionEnvelope adapter for the bounded Agent Harness."""

from __future__ import annotations

import json
import os
from typing import Protocol

from src.backend.app.schemas.agent_harness import ActionEnvelope


class AgentModelAdapter(Protocol):
    def propose_action(self, *, snapshot: dict, provider_ref: str, repair: bool = False) -> ActionEnvelope: ...


class DefaultAgentModelAdapter:
    """Small adapter that preserves deterministic planner availability.

    Rule-based and mock providers select the only productive initial action
    deterministically. An OpenAI-compatible provider must be explicitly
    configured; callers can safely fall back when it is not.
    """

    def propose_action(self, *, snapshot: dict, provider_ref: str, repair: bool = False) -> ActionEnvelope:
        state = str(snapshot.get("lifecycle_state") or "")
        provider = provider_ref.strip().casefold()
        if provider in {"rule_based", "mock"}:
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
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    )
