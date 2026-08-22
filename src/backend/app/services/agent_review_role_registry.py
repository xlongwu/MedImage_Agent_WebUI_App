"""The single versioned registry for G0 and future production reviewers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from src.backend.app.schemas.agent_eval import AgentReviewerKind


@dataclass(frozen=True)
class AgentReviewRole:
    reviewer_kind: AgentReviewerKind
    role_id: str
    prompt_template_version: str
    output_schema_version: str
    purpose: str


_REGISTRY: tuple[AgentReviewRole, ...] = (
    AgentReviewRole("science", "science_reviewer.v1", "science-review-prompt-v1", "agent-review-finding-v1", "science_prerequisites"),
    AgentReviewRole("safety", "safety_reviewer.v1", "safety-review-prompt-v1", "agent-review-finding-v1", "project_safety"),
    AgentReviewRole("completeness", "completeness_reviewer.v1", "completeness-review-prompt-v1", "agent-review-finding-v1", "goal_coverage"),
)


def role_registry() -> tuple[AgentReviewRole, ...]:
    return _REGISTRY


def role_for(kind: AgentReviewerKind) -> AgentReviewRole:
    return next(role for role in _REGISTRY if role.reviewer_kind == kind)


def role_registry_hash() -> str:
    payload = [role.__dict__ for role in _REGISTRY]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
