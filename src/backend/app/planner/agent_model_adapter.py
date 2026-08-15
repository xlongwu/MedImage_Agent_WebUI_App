"""Provider-neutral ActionEnvelope adapter for the bounded Agent Harness."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from src.backend.app.agent_skills.schemas import SkillContextRef
from src.backend.app.schemas.agent_harness import (
    ActionEnvelope,
    CanonicalModelRequest,
    DraftPlanAction,
    action_envelope_json_schema,
    parse_action_envelope_json,
)

CONTEXT_V3_SECTION_ORDER = (
    "goal", "policy", "project_evidence", "decision_state", "plan_state",
    "execution_state", "latest_observation", "last_action_result", "memory_context", "budget",
)


def _canonical_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    return value


def serialize_context_v3(snapshot: dict) -> dict:
    """Validate and serialize Context v3 in a provider-cache-stable order.

    This is intentionally the sole adapter boundary.  It accepts no legacy
    flat snapshot, so a stored v1 row cannot silently reach a model provider.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 3:
        raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
    if snapshot.get("complete") is not True:
        raise ValueError("AGENT_CONTEXT_INCOMPLETE")
    purpose = snapshot.get("purpose")
    if purpose not in {"decision_request", "plan_draft"}:
        raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
    required = snapshot.get("required_sections")
    included = snapshot.get("included_sections")
    if not isinstance(required, list) or not isinstance(included, list):
        raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
    if not set(required).issubset(included) or not set(included).issubset(CONTEXT_V3_SECTION_ORDER):
        raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
    sections = snapshot.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(included):
        raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
    fixed_sections: dict[str, object] = {}
    for name in CONTEXT_V3_SECTION_ORDER:
        if name not in sections:
            continue
        section = sections[name]
        if not isinstance(section, dict) or section.get("schema_version") != 1:
            raise ValueError("AGENT_CONTEXT_SCHEMA_INVALID")
        fixed_sections[name] = _canonical_value(section)
    skill_refs = tuple(
        SkillContextRef.model_validate(item)
        for item in snapshot.get("skill_refs", [])
        if isinstance(item, dict)
    )
    allowed_sections = {
        section for reference in skill_refs for section in reference.sections
    }
    if skill_refs:
        fixed_sections = {
            name: section for name, section in fixed_sections.items() if name in allowed_sections
        }
    return {
        "schema_version": 3,
        "purpose": purpose,
        "required_sections": list(required),
        "included_sections": [name for name in CONTEXT_V3_SECTION_ORDER if name in included],
        "omitted_sections": sorted(str(item) for item in snapshot.get("omitted_sections", []) if isinstance(item, str)),
        "evidence_refs": sorted(
            [item for item in snapshot.get("evidence_refs", []) if isinstance(item, dict)],
            key=lambda item: (str(item.get("type") or ""), str(item.get("record_id") or ""), str(item.get("hash") or "")),
        ),
        "evidence_snapshot_hash": str(snapshot.get("evidence_snapshot_hash") or ""),
        "projection_policy_version": str(snapshot.get("projection_policy_version") or ""),
        "complete": True,
        "policy_version": str(snapshot.get("policy_version") or ""),
        "redaction_policy_version": str(snapshot.get("redaction_policy_version") or ""),
        "prompt_template_version": str(snapshot.get("prompt_template_version") or ""),
        "skill_refs": [reference.model_dump(mode="json") for reference in sorted(
            skill_refs, key=lambda reference: (reference.skill_id, reference.content_hash)
        )],
        "skill_error_codes": sorted(
            str(item) for item in snapshot.get("skill_error_codes", []) if isinstance(item, str)
        ),
        "sections": fixed_sections,
    }


@dataclass(frozen=True)
class ActionCallMetadata:
    provider: str
    model: str | None
    endpoint_class: str
    response_hash: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    latency_ms: int | None
    provider_request_id: str | None
    network_called: bool


@dataclass(frozen=True)
class ActionProposal:
    envelope: ActionEnvelope
    metadata: ActionCallMetadata

    @classmethod
    def rule_based(cls, envelope: ActionEnvelope) -> ActionProposal:
        return cls(
            envelope=envelope,
            metadata=ActionCallMetadata(
                provider="rule_based", model=None, endpoint_class="rule_based",
                response_hash=None, input_tokens=None, output_tokens=None,
                cached_input_tokens=None, latency_ms=None, provider_request_id=None,
                network_called=False,
            ),
        )


class AgentModelProviderError(RuntimeError):
    def __init__(self, code: str, metadata: ActionCallMetadata) -> None:
        super().__init__(code)
        self.code = code
        self.metadata = metadata


class AgentModelInvalidOutputError(AgentModelProviderError):
    pass


class AgentModelAdapter(Protocol):
    def propose_action(self, *, request: CanonicalModelRequest) -> ActionProposal: ...


REQUEST_BUILDER_VERSION = "agent-harness-request-v1"
_SYSTEM_PROMPT = (
    "You are an advice-only research planning assistant. You may not approve, execute, "
    "write files, invoke tools, or issue commands. Return strictly valid JSON."
)


def canonical_request_bytes(request: CanonicalModelRequest) -> bytes:
    """Stable serialization used for the request hash and byte count."""
    return json.dumps(
        _canonical_value(request.model_dump(mode="json")),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_canonical_model_request(
    *, snapshot: dict, provider_ref: str, repair: bool,
) -> CanonicalModelRequest:
    """Build the sole object from which a provider request may be sent."""
    serialized = serialize_context_v3(snapshot)
    refs = tuple(SkillContextRef.model_validate(item) for item in serialized["skill_refs"])
    from src.backend.app.agent_skills.loader import AgentSkillLoader

    skill_result = AgentSkillLoader().render(refs)
    provider = provider_ref.strip().casefold()
    if provider == "rule_based":
        model = None
        endpoint_class = "rule_based"
        parameters: dict[str, object] = {
            "temperature": 0,
            "max_output_tokens": 0,
            "timeout_seconds": 0,
            "response_format": "typed_local",
        }
    else:
        from src.backend.app.planner.llm_provider import get_openai_compatible_action_request_config

        config = get_openai_compatible_action_request_config()
        model = config.model
        endpoint_class = "chat_completions"
        parameters = {
            "temperature": 0,
            "max_output_tokens": 1024,
            "timeout_seconds": 60,
            "response_format": {"type": "json_object"},
        }
    return CanonicalModelRequest(
        provider=provider or "unknown",
        model=model,
        endpoint_class=endpoint_class,
        prompt_template_version=str(serialized["prompt_template_version"]),
        system_prompt=_SYSTEM_PROMPT,
        context_payload={
            "repair_instruction": (
                "Your previous reply was invalid. Return one corrected JSON object."
                if repair else "Return one JSON object."
            ),
            "skill_working_procedures": skill_result.markdown or (
                "No packaged procedure is available; follow the base safety policy only."
            ),
            "safe_context": serialized,
        },
        action_schema=action_envelope_json_schema(),
        model_parameters=parameters,
        repair=repair,
    )


class DefaultAgentModelAdapter:
    """Small adapter for an explicitly selected Harness provider.

    The rule-based provider selects the only productive initial action
    deterministically. An OpenAI-compatible provider must be explicitly
    configured; provider failure stops the enabled Harness.
    """

    def propose_action(self, *, request: CanonicalModelRequest) -> ActionProposal:
        raw_context = request.context_payload.get("safe_context")
        raw_sections = raw_context.get("sections") if isinstance(raw_context, dict) else None
        raw_goal = raw_sections.get("goal") if isinstance(raw_sections, dict) else None
        raw_goal_data = raw_goal.get("data") if isinstance(raw_goal, dict) else None
        state = str(raw_goal_data.get("lifecycle_state") or "") if isinstance(raw_goal_data, dict) else ""
        provider = request.provider
        if provider == "rule_based":
            if state not in {"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"}:
                raise AgentModelProviderError(
                    "AGENT_HARNESS_ACTION_UNAVAILABLE",
                    ActionCallMetadata(
                        provider="rule_based", model=None, endpoint_class="rule_based",
                        response_hash=None, input_tokens=None, output_tokens=None,
                        cached_input_tokens=None, latency_ms=None, provider_request_id=None,
                        network_called=False,
                    ),
                )
            return ActionProposal.rule_based(DraftPlanAction(
                kind="draft_plan",
                reason="Use the existing deterministic reviewed-planning service.",
                expected_state=state,
            ))
        if provider != "openai_compatible" or not os.environ.get("MEDIMAGE_LLM_API_KEY"):
            raise AgentModelProviderError(
                "AGENT_HARNESS_PROVIDER_UNAVAILABLE",
                ActionCallMetadata(
                    provider="openai_compatible", model=None, endpoint_class="chat_completions",
                    response_hash=None, input_tokens=None, output_tokens=None,
                    cached_input_tokens=None, latency_ms=None, provider_request_id=None,
                    network_called=False,
                ),
            )
        from src.backend.app.planner.audit_record import stable_hash
        from src.backend.app.planner.llm_provider import call_openai_compatible_action_provider

        result = call_openai_compatible_action_provider(request=request)
        metadata = ActionCallMetadata(
            provider="openai_compatible", model=result.model,
            endpoint_class=result.endpoint_class, response_hash=(stable_hash(result.content) if result.content else None),
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cached_input_tokens=result.cached_input_tokens, latency_ms=result.latency_ms,
            provider_request_id=result.provider_request_id, network_called=result.network_called,
        )
        if not result.ok:
            code = result.errors[0] if result.errors else "AGENT_HARNESS_MODEL_FAILED"
            error_type = AgentModelInvalidOutputError if code == "AGENT_HARNESS_MODEL_OUTPUT_INVALID" else AgentModelProviderError
            raise error_type(code, metadata)
        try:
            return ActionProposal(envelope=parse_action_envelope_json(result.content), metadata=metadata)
        except (ValueError, TypeError) as exc:
            raise AgentModelInvalidOutputError("AGENT_HARNESS_MODEL_OUTPUT_INVALID", metadata) from exc


def action_schema() -> dict[str, object]:
    """Expose the exact JSON Schema without passing executable tool metadata."""
    return action_envelope_json_schema()


def build_action_prompt(request: CanonicalModelRequest) -> str:
    """Serialize only fields already present in the canonical request."""
    return (
        json.dumps(
            {"context_payload": request.context_payload, "action_schema": request.action_schema},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
