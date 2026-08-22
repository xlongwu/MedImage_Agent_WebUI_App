from __future__ import annotations

import pytest

from src.backend.app.agent_skills.registry import AgentSkillRegistry, BUILTIN_SKILL_IDS
from src.backend.app.core.config import ConfigService
from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.agent_model_adapter import (
    REQUEST_BUILDER_VERSION,
    action_schema,
    build_canonical_model_request,
)
from src.backend.app.schemas.agent_model import build_agent_model_profile


def _refs():
    registry = AgentSkillRegistry()
    return tuple(registry.load(skill_id).reference for skill_id in BUILTIN_SKILL_IDS)


def test_model_profile_is_stable_and_never_hashes_the_secret() -> None:
    common = dict(
        enabled=True,
        provider="openai_compatible",
        model="agent-test-model",
        base_url="https://models.example.test/v1/",
    )
    first = build_agent_model_profile(
        AgentModelRuntimeConfig(**common, api_key="first-secret"),
        prompt_template_version="agent-harness-prompt-v3",
        skill_refs=_refs(),
        action_schema=action_schema(),
        context_policy_version="agent-context-v3",
        request_builder_version=REQUEST_BUILDER_VERSION,
    )
    same_identity = build_agent_model_profile(
        AgentModelRuntimeConfig(**common, api_key="second-secret"),
        prompt_template_version="agent-harness-prompt-v3",
        skill_refs=_refs(),
        action_schema=action_schema(),
        context_policy_version="agent-context-v3",
        request_builder_version=REQUEST_BUILDER_VERSION,
    )
    changed_model = build_agent_model_profile(
        AgentModelRuntimeConfig(**{**common, "model": "other-model"}, api_key="first-secret"),
        prompt_template_version="agent-harness-prompt-v3",
        skill_refs=_refs(),
        action_schema=action_schema(),
        context_policy_version="agent-context-v3",
        request_builder_version=REQUEST_BUILDER_VERSION,
    )

    assert first.profile_hash == same_identity.profile_hash
    assert first.profile_hash != changed_model.profile_hash
    assert "first-secret" not in first.model_dump_json()
    assert "models.example.test" not in first.model_dump_json()


def test_config_snapshot_exposes_only_the_public_model_projection(monkeypatch) -> None:
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_NAME", "agent-test-model")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_API_KEY", "profile-secret")

    snapshot = ConfigService().snapshot().model

    assert snapshot.provider == "openai_compatible"
    assert snapshot.api_key_configured is True
    assert "profile-secret" not in snapshot.model_dump_json()


def test_invalid_model_environment_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_PROVIDER", "unknown-provider")

    with pytest.raises(ValueError, match="AGENT_MODEL_CONFIG_INVALID"):
        AgentModelRuntimeConfig.from_env()

    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_PROVIDER", "rule_based")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_TIMEOUT_SECONDS", "not-a-number")

    with pytest.raises(ValueError, match="MEDIMAGE_AGENT_MODEL_TIMEOUT_SECONDS"):
        AgentModelRuntimeConfig.from_env()


def test_canonical_request_binds_the_runtime_profile() -> None:
    config = AgentModelRuntimeConfig()
    request = build_canonical_model_request(
        config=config,
        repair=False,
        snapshot={
            "schema_version": 3,
            "purpose": "plan_draft",
            "complete": True,
            "required_sections": ["goal", "policy"],
            "included_sections": ["goal", "policy"],
            "omitted_sections": [],
            "evidence_refs": [],
            "evidence_snapshot_hash": "evidence",
            "projection_policy_version": "agent-context-v3",
            "policy_version": "policy",
            "redaction_policy_version": "redaction",
            "prompt_template_version": "agent-harness-prompt-v3",
            "skill_refs": [],
            "skill_error_codes": [],
            "sections": {
                name: {"schema_version": 1, "source_hash": name, "source_refs": [], "data": {}}
                for name in ("goal", "policy")
            },
        },
    )

    assert len(request.model_profile_hash) == 64
    assert request.provider == "rule_based"
