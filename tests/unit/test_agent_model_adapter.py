from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.agent_model_adapter import (
    MAX_COMPLETE_REQUEST_BYTES,
    AgentModelProviderError,
    AgentModelRequestLimitExceededError,
    DefaultAgentModelAdapter,
    assert_complete_request_within_limit,
    complete_request_bytes,
)
from src.backend.app.planner.llm_provider import LLMProviderResult
from src.backend.app.schemas.agent_harness import CanonicalModelRequest
from src.backend.app.services.structured_agent_model_adapter import (
    StructuredAgentModelAdapter,
    StructuredModelCallError,
)


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
        "src.backend.app.services.structured_agent_model_adapter.StructuredAgentModelAdapter.propose_action",
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


def test_shared_structured_review_validates_role_output_and_preserves_metadata() -> None:
    class Provider:
        def complete(self, *, request: CanonicalModelRequest) -> LLMProviderResult:
            return LLMProviderResult(
                ok=True,
                content=json.dumps([{
                    "reviewer_kind": "science",
                    "severity": "warning",
                    "code": "SCIENCE_GAP",
                    "message_key": "review.science.gap",
                    "input_refs": ["section:goal"],
                }]),
                model=request.model,
                input_tokens=15,
                output_tokens=8,
                cached_input_tokens=2,
                latency_ms=31,
                provider_request_id="request-1",
                network_called=True,
            )

    findings, metadata = StructuredAgentModelAdapter(Provider()).review(
        request=_request(provider="openai_compatible"),
        reviewer_kind="science",
    )
    assert len(findings) == 1
    assert findings[0].code == "SCIENCE_GAP"
    assert metadata.provider_id == "openai_compatible"
    assert metadata.input_tokens == 15 and metadata.cached_input_tokens == 2
    assert metadata.response_hash is not None and metadata.network_called is True


def test_shared_structured_review_rejects_wrong_role() -> None:
    class Provider:
        def complete(self, *, request: CanonicalModelRequest) -> LLMProviderResult:
            return LLMProviderResult(
                ok=True,
                content=json.dumps([{
                    "reviewer_kind": "safety",
                    "severity": "warning",
                    "code": "SCIENCE_GAP",
                    "message_key": "review.science.gap",
                    "input_refs": ["section:goal"],
                }]),
                network_called=True,
            )

    with pytest.raises(StructuredModelCallError) as exc_info:
        StructuredAgentModelAdapter(Provider()).review(
            request=_request(provider="openai_compatible"),
            reviewer_kind="science",
        )
    assert exc_info.value.code == "MULTI_AGENT_REVIEWER_KIND_MISMATCH"


@pytest.mark.parametrize("repair", [False, True])
def test_complete_request_budget_includes_output_reservation(repair: bool) -> None:
    request = _request(provider="rule_based").model_copy(update={
        "context_payload": {"skill_working_procedures": "x" * MAX_COMPLETE_REQUEST_BYTES},
        "model_parameters": {"max_output_tokens": 1024},
        "repair": repair,
    })

    request_bytes, reserved_output_bytes, total_bytes = complete_request_bytes(request)
    assert request_bytes > MAX_COMPLETE_REQUEST_BYTES
    assert reserved_output_bytes == 4096
    assert total_bytes == request_bytes + reserved_output_bytes
    with pytest.raises(AgentModelRequestLimitExceededError) as exc_info:
        assert_complete_request_within_limit(request)
    assert exc_info.value.code == "AGENT_MODEL_REQUEST_TOO_LARGE"
