from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


class ServerConfig(BaseModel):
    """Backend server configuration loaded from MEDIMAGE_* env vars."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    service_name: str = "medimage-agent-backend"
    api_version: str = "0.1.0"
    log_level: str = "INFO"
    agent_approval_token: str | None = None
    agent_approval_actor: str | None = None
    desktop_session_token: str | None = None

    @classmethod
    def from_env(cls) -> ServerConfig:
        raw_port = os.environ.get("MEDIMAGE_BACKEND_PORT", "8000")
        try:
            port = int(raw_port)
        except ValueError:
            port = 8000
        if port < 1 or port > 65535:
            port = 8000
        return cls(
            host=os.environ.get("MEDIMAGE_BACKEND_HOST", "127.0.0.1"),
            port=port,
            service_name=os.environ.get("MEDIMAGE_SERVICE_NAME", "medimage-agent-backend"),
            api_version=os.environ.get("MEDIMAGE_API_VERSION", "0.1.0"),
            log_level=os.environ.get("MEDIMAGE_LOG_LEVEL", "INFO"),
            agent_approval_token=(
                os.environ.get("MEDIMAGE_AGENT_APPROVAL_TOKEN", "").strip() or None
            ),
            agent_approval_actor=(
                os.environ.get("MEDIMAGE_AGENT_APPROVAL_ACTOR", "").strip() or None
            ),
            desktop_session_token=(
                os.environ.get("MEDIMAGE_DESKTOP_SESSION_TOKEN", "").strip() or None
            ),
        )


class MemoryConfig(BaseModel):
    """Installation-level Memory Domain gates and safe local storage settings."""

    enabled: bool = False
    generation_enabled: bool = False
    use_enabled: bool = False
    llm_extraction_enabled: bool = False
    llm_consolidation_enabled: bool = False
    projection_enabled: bool = False
    store_path: str
    max_context_bytes: int = Field(default=16384, ge=1024, le=262144)
    candidate_retention_days: int = Field(default=30, ge=1, le=3650)
    item_retention_days: int = Field(default=180, ge=1, le=3650)

    @classmethod
    def from_env(cls) -> MemoryConfig:
        desktop_path = Path(
            os.environ.get(
                "MEDIMAGE_DESKTOP_STORE_PATH",
                "outputs/work/desktop/desktop_state.sqlite",
            )
        ).expanduser()
        default_path = desktop_path.parent / "memory_state.sqlite"
        raw_budget = os.environ.get("MEDIMAGE_MEMORY_MAX_CONTEXT_BYTES", "16384")
        try:
            budget = int(raw_budget)
        except ValueError:
            budget = 16384
        if budget < 1024 or budget > 262144:
            budget = 16384
        def retention_days(name: str, default: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError:
                return default
            return value if 1 <= value <= 3650 else default

        return cls(
            enabled=_env_bool("MEDIMAGE_MEMORY_ENABLED"),
            generation_enabled=_env_bool("MEDIMAGE_MEMORY_GENERATION_ENABLED"),
            use_enabled=_env_bool("MEDIMAGE_MEMORY_USE_ENABLED"),
            llm_extraction_enabled=_env_bool("MEDIMAGE_MEMORY_LLM_EXTRACTION_ENABLED"),
            llm_consolidation_enabled=_env_bool(
                "MEDIMAGE_MEMORY_LLM_CONSOLIDATION_ENABLED"
            ),
            projection_enabled=_env_bool("MEDIMAGE_MEMORY_PROJECTION_ENABLED"),
            store_path=str(
                Path(os.environ.get("MEDIMAGE_MEMORY_STORE_PATH", default_path))
                .expanduser()
                .resolve()
            ),
            max_context_bytes=budget,
            candidate_retention_days=retention_days(
                "MEDIMAGE_MEMORY_CANDIDATE_RETENTION_DAYS", 30
            ),
            item_retention_days=retention_days(
                "MEDIMAGE_MEMORY_ITEM_RETENTION_DAYS", 180
            ),
        )


class AgentHarnessConfig(BaseModel):
    """Installation-level limits for the optional advice-only Harness.

    Invalid environment values always resolve to the conservative defaults;
    enabling the feature never enables any execution capability.
    """

    enabled: bool = False
    max_steps: int = Field(default=8, ge=1, le=16)
    max_model_calls: int = Field(default=6, ge=1, le=6)
    max_action_proposals: int = Field(default=8, ge=1, le=8)
    max_repairs: int = Field(default=1, ge=0, le=1)
    max_wall_seconds: int = Field(default=300, ge=1, le=300)
    max_input_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    lease_seconds: int = Field(default=30, ge=5, le=300)
    max_steps_per_wakeup: int = Field(default=3, ge=1, le=6)

    @classmethod
    def from_env(cls) -> AgentHarnessConfig:
        def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError:
                return default
            return value if minimum <= value <= maximum else default

        def optional_bounded(name: str, maximum: int) -> int | None:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return None
            try:
                value = int(raw)
            except ValueError:
                return None
            return value if 1 <= value <= maximum else None

        return cls(
            enabled=_env_bool("MEDIMAGE_AGENT_HARNESS_ENABLED"),
            max_steps=bounded("MEDIMAGE_AGENT_HARNESS_MAX_STEPS", 8, 1, 16),
            max_model_calls=bounded("MEDIMAGE_AGENT_HARNESS_MAX_MODEL_CALLS", 6, 1, 6),
            max_action_proposals=bounded("MEDIMAGE_AGENT_HARNESS_MAX_ACTION_PROPOSALS", 8, 1, 8),
            max_repairs=bounded("MEDIMAGE_AGENT_HARNESS_MAX_REPAIRS", 1, 0, 1),
            max_wall_seconds=bounded("MEDIMAGE_AGENT_HARNESS_MAX_WALL_SECONDS", 300, 1, 300),
            max_input_tokens=optional_bounded("MEDIMAGE_AGENT_HARNESS_MAX_INPUT_TOKENS", 1_000_000),
            max_output_tokens=optional_bounded("MEDIMAGE_AGENT_HARNESS_MAX_OUTPUT_TOKENS", 1_000_000),
            lease_seconds=bounded("MEDIMAGE_AGENT_HARNESS_LEASE_SECONDS", 30, 5, 300),
            max_steps_per_wakeup=bounded("MEDIMAGE_AGENT_HARNESS_MAX_STEPS_PER_WAKEUP", 3, 1, 6),
        )


class AgentModelRuntimeConfig(BaseModel):
    """The one private configuration source for Agent model calls."""

    enabled: bool = False
    provider: Literal["rule_based", "openai_compatible"] = "rule_based"
    model: str | None = None
    base_url: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    max_output_tokens: int = Field(default=1024, ge=128, le=4096)
    api_key: SecretStr | None = None

    def incomplete_reason(self) -> str | None:
        if self.provider == "rule_based":
            return None
        if not self.enabled or not self.model or not self.base_url or self.api_key is None:
            return "AGENT_MODEL_CONFIG_INCOMPLETE"
        return None

    @classmethod
    def from_env(cls) -> AgentModelRuntimeConfig:
        provider = os.environ.get("MEDIMAGE_AGENT_MODEL_PROVIDER", "rule_based").strip().casefold()
        if provider not in {"rule_based", "openai_compatible"}:
            provider = "rule_based"

        def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError:
                return default
            return value if minimum <= value <= maximum else default

        model = os.environ.get("MEDIMAGE_AGENT_MODEL_NAME", "").strip() or None
        base_url = os.environ.get("MEDIMAGE_AGENT_MODEL_BASE_URL", "").strip() or None
        api_key = os.environ.get("MEDIMAGE_AGENT_MODEL_API_KEY", "").strip() or None
        return cls(
            enabled=_env_bool("MEDIMAGE_AGENT_MODEL_ENABLED"),
            provider=provider,
            model=model,
            base_url=base_url,
            timeout_seconds=bounded("MEDIMAGE_AGENT_MODEL_TIMEOUT_SECONDS", 60, 1, 120),
            max_output_tokens=bounded("MEDIMAGE_AGENT_MODEL_MAX_OUTPUT_TOKENS", 1024, 128, 4096),
            api_key=SecretStr(api_key) if api_key else None,
        )


class AgentModelPublicConfig(BaseModel):
    """Safe model configuration projection for state and diagnostics."""

    enabled: bool
    provider: Literal["rule_based", "openai_compatible"]
    model: str | None
    endpoint_class: str
    api_key_configured: bool

    @classmethod
    def from_runtime(cls, config: AgentModelRuntimeConfig) -> AgentModelPublicConfig:
        return cls(
            enabled=config.enabled,
            provider=config.provider,
            model=config.model,
            endpoint_class="rule_based" if config.provider == "rule_based" else "chat_completions",
            api_key_configured=config.api_key is not None,
        )


class AppConfig(BaseModel):
    """Top-level configuration snapshot exposed by ConfigService."""

    server: ServerConfig
    memory: MemoryConfig
    harness: AgentHarnessConfig = Field(default_factory=AgentHarnessConfig)
    model: AgentModelPublicConfig
    project: dict[str, Any] | None = None
    project_config_path: str | None = None
