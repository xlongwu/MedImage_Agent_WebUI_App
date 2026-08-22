from __future__ import annotations

from pydantic import SecretStr
import pytest

from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.agent_model_adapter import (
    AgentModelProviderError,
    DefaultAgentModelAdapter,
)
from src.backend.app.schemas.agent_harness import CanonicalModelRequest


def _request(*, provider: str) -> CanonicalModelRequest:
    return CanonicalModelRequest(
        provider=provider,
        model=None,
        endpoint_class="rule_based" if provider == "rule_based" else "chat_completions",
        prompt_template_version="agent-harness-prompt-v3",
        system_prompt="Return one safe action.",
        context_payload={
            "safe_context": {
                "sections": {
                    "goal": {"data": {"lifecycle_state": "CREATED"}},
                }
            }
        },
        action_schema={},
        model_parameters={},
        model_profile_hash="a" * 64,
    )


def test_default_rule_based_adapter_never_calls_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(**_kwargs):
        raise AssertionError("rule-based adapter must not call a network provider")

    monkeypatch.setattr(
        "src.backend.app.planner.llm_provider.call_openai_compatible_action_provider",
        fail_network,
    )
    proposal = DefaultAgentModelAdapter(
        config=AgentModelRuntimeConfig()
    ).propose_action(request=_request(provider="rule_based"))

    assert proposal.envelope.kind == "draft_plan"
    assert proposal.metadata.network_called is False


def test_incomplete_openai_compatible_config_fails_without_network() -> None:
    adapter = DefaultAgentModelAdapter(
        config=AgentModelRuntimeConfig(provider="openai_compatible")
    )

    with pytest.raises(AgentModelProviderError) as exc_info:
        adapter.propose_action(request=_request(provider="openai_compatible"))

    assert exc_info.value.code == "AGENT_MODEL_CONFIG_INCOMPLETE"
    assert exc_info.value.metadata.network_called is False


def test_runtime_config_redacts_api_key() -> None:
    secret = "test-secret-must-not-leak"
    config = AgentModelRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        model="test-model",
        base_url="https://provider.invalid/v1",
        api_key=SecretStr(secret),
    )

    assert secret not in repr(config)
    assert secret not in config.model_dump_json()
