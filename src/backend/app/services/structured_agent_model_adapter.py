"""Strict structured reviewer adapter seam shared by G0 and a future Team runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.backend.app.schemas.agent_eval import AgentReviewFinding
from src.backend.app.services.agent_review_context_projector import AgentReviewRoleContext


@dataclass(frozen=True)
class StructuredReviewMetadata:
    response_hash: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    provider_request_id: str | None
    network_called: bool


class StructuredReviewProvider(Protocol):
    def propose_review(self, *, context: AgentReviewRoleContext) -> tuple[object, StructuredReviewMetadata]: ...


class StructuredAgentModelAdapter:
    """Validate the only review result shape before it reaches aggregation."""

    def __init__(self, provider: StructuredReviewProvider) -> None:
        self._provider = provider

    def review(self, *, context: AgentReviewRoleContext) -> tuple[tuple[AgentReviewFinding, ...], StructuredReviewMetadata]:
        payload, metadata = self._provider.propose_review(context=context)
        if not isinstance(payload, list | tuple) or len(payload) > 8:
            raise ValueError("MULTI_AGENT_REVIEW_OUTPUT_INVALID")
        findings = tuple(AgentReviewFinding.model_validate(item) for item in payload)
        if any(item.reviewer_kind != context.reviewer_kind for item in findings):
            raise ValueError("MULTI_AGENT_REVIEWER_KIND_MISMATCH")
        return findings, metadata
