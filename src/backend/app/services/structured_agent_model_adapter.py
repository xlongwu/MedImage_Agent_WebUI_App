"""Shared structured provider seam for Harness actions and reviewer findings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.llm_provider import LLMProviderResult, call_openai_compatible_chat
from src.backend.app.schemas.agent_eval import AgentReviewerKind, AgentReviewFinding
from src.backend.app.schemas.agent_harness import (
    ActionEnvelope,
    CanonicalModelRequest,
    parse_action_envelope_json,
)
from src.backend.app.schemas.agent_model import build_agent_model_profile


@dataclass(frozen=True)
class StructuredReviewMetadata:
    provider_id: str
    model_id: str | None
    endpoint_class: str
    response_hash: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    latency_ms: int | None
    provider_request_id: str | None
    network_called: bool


class StructuredReviewProvider(Protocol):
    def complete(self, *, request: CanonicalModelRequest) -> LLMProviderResult: ...


class StructuredModelCallError(RuntimeError):
    def __init__(self, code: str, metadata: StructuredReviewMetadata) -> None:
        super().__init__(code)
        self.code = code
        self.metadata = metadata


class OpenAICompatibleStructuredProvider:
    """Send one canonical JSON request through the shared chat transport."""

    def __init__(self, config: AgentModelRuntimeConfig, *, http_post=None) -> None:
        self.config = config
        self.http_post = http_post

    def complete(self, *, request: CanonicalModelRequest) -> LLMProviderResult:
        parameters = request.model_parameters
        temperature = parameters.get("temperature", 0)
        max_output_tokens = parameters.get("max_output_tokens", self.config.max_output_tokens)
        if not isinstance(temperature, int | float) or isinstance(temperature, bool):
            return LLMProviderResult(ok=False, content="", errors=["AGENT_MODEL_CONFIG_INVALID"])
        if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool):
            return LLMProviderResult(ok=False, content="", errors=["AGENT_MODEL_CONFIG_INVALID"])
        response_format = parameters.get("response_format", {"type": "json_object"})
        if not isinstance(response_format, dict):
            return LLMProviderResult(ok=False, content="", errors=["AGENT_MODEL_CONFIG_INVALID"])
        return call_openai_compatible_chat(
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": build_structured_prompt(request)},
            ],
            config=self.config,
            temperature=float(temperature),
            max_output_tokens=max_output_tokens,
            response_format=response_format,
            http_post=self.http_post,
        )


class StructuredAgentModelAdapter:
    """Validate typed outputs and return provider metadata without owning ledgers."""

    def __init__(self, provider: StructuredReviewProvider) -> None:
        self._provider = provider

    def propose_action(
        self, *, request: CanonicalModelRequest
    ) -> tuple[ActionEnvelope, StructuredReviewMetadata]:
        result, metadata = self._complete(request=request)
        try:
            return parse_action_envelope_json(result.content), metadata
        except (ValueError, TypeError) as exc:
            raise StructuredModelCallError("AGENT_HARNESS_MODEL_OUTPUT_INVALID", metadata) from exc

    def review(
        self,
        *,
        request: CanonicalModelRequest,
        reviewer_kind: AgentReviewerKind,
    ) -> tuple[tuple[AgentReviewFinding, ...], StructuredReviewMetadata]:
        result, metadata = self._complete(request=request)
        try:
            payload = json.loads(result.content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise StructuredModelCallError("MULTI_AGENT_REVIEW_OUTPUT_INVALID", metadata) from exc
        if not isinstance(payload, list) or len(payload) > 8:
            raise StructuredModelCallError("MULTI_AGENT_REVIEW_OUTPUT_INVALID", metadata)
        try:
            findings = tuple(AgentReviewFinding.model_validate(item) for item in payload)
        except (ValueError, TypeError) as exc:
            raise StructuredModelCallError("MULTI_AGENT_REVIEW_OUTPUT_INVALID", metadata) from exc
        if any(item.reviewer_kind != reviewer_kind for item in findings):
            raise StructuredModelCallError("MULTI_AGENT_REVIEWER_KIND_MISMATCH", metadata)
        return findings, metadata

    def _complete(
        self, *, request: CanonicalModelRequest
    ) -> tuple[LLMProviderResult, StructuredReviewMetadata]:
        result = self._provider.complete(request=request)
        metadata = StructuredReviewMetadata(
            provider_id=request.provider,
            model_id=result.model or request.model,
            endpoint_class=result.endpoint_class or request.endpoint_class,
            response_hash=(
                hashlib.sha256(result.content.encode("utf-8")).hexdigest()
                if result.content else None
            ),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_input_tokens=result.cached_input_tokens,
            latency_ms=result.latency_ms,
            provider_request_id=result.provider_request_id,
            network_called=result.network_called,
        )
        if not result.ok:
            code = result.errors[0] if result.errors else "AGENT_MODEL_PROVIDER_FAILED"
            raise StructuredModelCallError(code, metadata)
        return result, metadata


def build_structured_prompt(request: CanonicalModelRequest) -> str:
    """Serialize only the fields included in the canonical provider request."""
    return json.dumps(
        {"context_payload": request.context_payload, "action_schema": request.action_schema},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def team_review_output_schema() -> dict[str, object]:
    return {
        "type": "array",
        "items": AgentReviewFinding.model_json_schema(),
        "maxItems": 8,
    }


def team_review_model_profile_hash(
    *, config: AgentModelRuntimeConfig, max_output_tokens: int
) -> str:
    effective_config = config.model_copy(
        update={"max_output_tokens": min(config.max_output_tokens, max_output_tokens)}
    )
    profile = build_agent_model_profile(
        effective_config,
        prompt_template_version="agent-team-review-envelope-v1",
        skill_refs=(),
        action_schema=team_review_output_schema(),
        context_policy_version="agent-team-role-context-v1",
        request_builder_version="agent-team-review-request-v1",
    )
    return profile.profile_hash
