from __future__ import annotations

from datetime import UTC, datetime

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.schemas.agent_harness import AgentHarnessStep, ModelCallRecord


def test_harness_budget_defaults_and_hard_limits_are_installation_scoped(monkeypatch) -> None:
    monkeypatch.setenv("MEDIMAGE_AGENT_HARNESS_MAX_STEPS", "17")
    monkeypatch.setenv("MEDIMAGE_AGENT_HARNESS_MAX_ACTION_PROPOSALS", "9")
    monkeypatch.setenv("MEDIMAGE_AGENT_HARNESS_MAX_INPUT_TOKENS", "not-a-number")

    config = AgentHarnessConfig.from_env()

    assert config.max_steps == 8
    assert config.max_action_proposals == 8
    assert config.max_input_tokens is None


def test_step_persists_only_redacted_nested_model_call_ledger() -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    call = ModelCallRecord(
        call_id="call-1", step_id="step-1", attempt_id="attempt-1", provider="openai_compatible", phase="planning",
        model="gpt-safe", endpoint_class="chat_completions", prompt_template_version="v2",
        context_hash="context-hash", request_hash="request-hash", action_schema_hash="schema-hash",
        model_parameters_hash="parameters-hash", request_bytes=100,
        request_builder_version="agent-harness-request-v1", response_schema_version=2, response_hash="response-hash",
        schema_valid=True, started_at=now, completed_at=now, latency_ms=5,
        input_tokens=10, output_tokens=4, cached_input_tokens=None, provider_request_id="req-1",
        network_called=True, status="succeeded",
    )
    step = AgentHarnessStep(
        step_id="step-1", attempt_id="attempt-1", project_id="project-1", step_no=1,
        idempotency_key="idem-1", input_hash="input-hash", validation_result="accepted",
        state_before="CREATED", started_at=now, model_calls=(call,),
    )

    payload = step.model_dump(mode="json")

    assert len(step.model_calls) == 1
    assert payload["model_calls"][0]["request_hash"] == "request-hash"
    assert "raw" not in payload["model_calls"][0]
