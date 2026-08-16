"""Redacted, hashable identity for an Agent model configuration."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from src.backend.app.agent_skills.schemas import SkillContextRef
from src.backend.app.core.config_schema import AgentModelRuntimeConfig


def _stable_hash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _endpoint_identity(base_url: str | None) -> tuple[str, str | None]:
    if not base_url:
        return "not_configured", None
    parsed = urlsplit(base_url.strip())
    endpoint_class = "chat_completions" if parsed.scheme and parsed.netloc else "custom"
    normalized = f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path.rstrip('/')}"
    return endpoint_class, _stable_hash(normalized)


class AgentModelProfile(BaseModel):
    """Non-secret model identity bound to planning and approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    provider: Literal["rule_based", "openai_compatible"]
    model: str | None = Field(default=None, max_length=128)
    endpoint_class: str
    endpoint_fingerprint: str | None = None
    request_builder_version: str
    prompt_template_version: str
    action_schema_hash: str
    model_parameters_hash: str
    skill_hashes: tuple[str, ...]
    context_policy_version: str
    profile_hash: str


def build_agent_model_profile(
    config: AgentModelRuntimeConfig,
    *,
    prompt_template_version: str,
    skill_refs: tuple[SkillContextRef, ...],
    action_schema: dict[str, object],
    context_policy_version: str,
    request_builder_version: str,
) -> AgentModelProfile:
    """Build a deterministic profile without serialising credentials or URLs."""

    endpoint_class, endpoint_fingerprint = (
        ("rule_based", None)
        if config.provider == "rule_based"
        else _endpoint_identity(config.base_url)
    )
    parameters = {
        "timeout_seconds": config.timeout_seconds if config.provider == "openai_compatible" else 0,
        "max_output_tokens": config.max_output_tokens if config.provider == "openai_compatible" else 0,
        "enabled": config.enabled,
    }
    payload = {
        "schema_version": 1,
        "provider": config.provider,
        "model": config.model if config.provider == "openai_compatible" else None,
        "endpoint_class": endpoint_class,
        "endpoint_fingerprint": endpoint_fingerprint,
        "request_builder_version": request_builder_version,
        "prompt_template_version": prompt_template_version,
        "action_schema_hash": _stable_hash(action_schema),
        "model_parameters_hash": _stable_hash(parameters),
        "skill_hashes": tuple(sorted(reference.content_hash for reference in skill_refs)),
        "context_policy_version": context_policy_version,
    }
    return AgentModelProfile(**payload, profile_hash=_stable_hash(payload))
