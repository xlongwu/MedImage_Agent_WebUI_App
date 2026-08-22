"""LLM Advisor safety gate — enforce advice-only, no-execution policy."""
from __future__ import annotations

from typing import Any

from src.backend.app.core.config import ConfigService
from src.backend.app.core.config_schema import AgentModelRuntimeConfig

SAFETY_FLAGS = {
    "advice_only": True,
    "requires_human_confirmation": True,
    "will_execute_pipeline": False,
    "will_modify_data": False,
    "clinical_conclusion": False,
}


def wrap_advisor_response(data: dict[str, Any], advisor_type: str, fallback: bool = False) -> dict[str, Any]:
    """Wrap any advisor output with mandatory safety flags."""
    result = dict(data)
    result.update(SAFETY_FLAGS)
    result["advisor_type"] = advisor_type
    result["fallback"] = fallback
    return result


def advisor_fallback(advisor_type: str) -> dict[str, Any]:
    """Deterministic fallback when LLM is not configured."""
    return wrap_advisor_response({
        "message": (
            f"LLM advisor '{advisor_type}' is not enabled. "
            "Enable and configure MEDIMAGE_AGENT_MODEL_* to use model-powered advice. "
            "The system continues to operate with deterministic pipeline execution."
        ),
        "suggestion": "Use deterministic tools (SessionDB, Insights, Error KB) for operational guidance.",
    }, advisor_type, fallback=True)


def is_llm_enabled(config: AgentModelRuntimeConfig | None = None) -> bool:
    """Check if LLM advisor is configured and enabled."""
    runtime = config or ConfigService().model
    return runtime.provider == "openai_compatible" and runtime.incomplete_reason() is None


def get_llm_config() -> AgentModelRuntimeConfig:
    """Return the sole validated model runtime configuration."""

    return ConfigService().model
