from __future__ import annotations

import json

from src.backend.app.advisor.protocol_advisor import advise_protocol
from src.backend.app.planner.llm_provider import LLMProviderResult


def test_advisor_uses_shared_agent_model_transport(monkeypatch) -> None:
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_NAME", "advisor-test")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_API_KEY", "test-secret")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_TIMEOUT_SECONDS", "19")

    observed: dict[str, object] = {}

    def fake_chat(**kwargs):
        observed.update(kwargs)
        return LLMProviderResult(
            ok=True,
            content=json.dumps(
                {
                    "recommended_pipeline_template": "shared-transport",
                    "parameter_suggestions": {},
                    "warnings": [],
                    "unsupported_items": [],
                }
            ),
            network_called=True,
        )

    monkeypatch.setattr(
        "src.backend.app.planner.llm_provider.call_openai_compatible_chat",
        fake_chat,
    )

    result = advise_protocol(task_goal="plan only")

    assert result["recommended_pipeline_template"] == "shared-transport"
    assert result["fallback"] is False
    assert observed["config"].timeout_seconds == 19
