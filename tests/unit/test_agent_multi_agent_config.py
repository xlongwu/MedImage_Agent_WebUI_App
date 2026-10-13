from __future__ import annotations

from src.backend.app.core.config import ConfigService
from src.backend.app.core.config_schema import AgentMultiAgentConfig


def test_multi_agent_config_defaults_off_and_uses_fixed_safe_limits(monkeypatch) -> None:
    names = (
        "MEDIMAGE_MULTI_AGENT_ENABLED",
        "MEDIMAGE_MULTI_AGENT_MAX_MODEL_CALLS",
        "MEDIMAGE_MULTI_AGENT_MAX_INPUT_TOKENS",
        "MEDIMAGE_MULTI_AGENT_MAX_OUTPUT_TOKENS_PER_CALL",
        "MEDIMAGE_MULTI_AGENT_MAX_ROLE_DEADLINE_SECONDS",
        "MEDIMAGE_MULTI_AGENT_MAX_TEAM_DEADLINE_SECONDS",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)

    config = AgentMultiAgentConfig.from_env()
    assert config.enabled is False
    assert config.max_roles == 3
    assert config.max_model_calls == 6
    assert config.max_repairs_per_role == 1
    assert config.max_input_tokens == 16384
    assert config.max_output_tokens_per_call == 1024
    assert config.max_role_deadline_seconds == 60
    assert config.max_team_deadline_seconds == 180
    assert ConfigService().snapshot().multi_agent == config


def test_multi_agent_config_reads_enablement_and_falls_back_on_invalid_limits(monkeypatch) -> None:
    monkeypatch.setenv("MEDIMAGE_MULTI_AGENT_ENABLED", "true")
    monkeypatch.setenv("MEDIMAGE_MULTI_AGENT_MAX_MODEL_CALLS", "2")
    monkeypatch.setenv("MEDIMAGE_MULTI_AGENT_MAX_INPUT_TOKENS", "0")
    monkeypatch.setenv("MEDIMAGE_MULTI_AGENT_MAX_OUTPUT_TOKENS_PER_CALL", "8192")
    monkeypatch.setenv("MEDIMAGE_MULTI_AGENT_MAX_ROLE_DEADLINE_SECONDS", "0")
    monkeypatch.setenv("MEDIMAGE_MULTI_AGENT_MAX_TEAM_DEADLINE_SECONDS", "999")

    config = AgentMultiAgentConfig.from_env()
    assert config.enabled is True
    assert config.max_model_calls == 6
    assert config.max_input_tokens == 16384
    assert config.max_output_tokens_per_call == 1024
    assert config.max_role_deadline_seconds == 60
    assert config.max_team_deadline_seconds == 180
