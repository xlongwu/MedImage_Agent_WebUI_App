"""Redacted, immutable execution-environment bindings for reviewed work."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EnvironmentCapabilityStatus = Literal["available", "disabled", "unavailable"]


class ToolCapabilitySnapshot(BaseModel):
    """One external tool fact, without its executable or install path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str = Field(min_length=1, max_length=128)
    status: EnvironmentCapabilityStatus
    version: str | None = Field(default=None, max_length=256)
    installation_path_hash: str | None = Field(default=None, max_length=128)
    configuration_hash: str = Field(min_length=1, max_length=128)
    error_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class BackendCapabilitySnapshot(BaseModel):
    """One plan-selected backend fact, without host-local configuration values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend_id: str = Field(min_length=1, max_length=128)
    status: EnvironmentCapabilityStatus
    version: str | None = Field(default=None, max_length=256)
    executable_path_hash: str | None = Field(default=None, max_length=128)
    configuration_hash: str = Field(min_length=1, max_length=128)
    error_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ExecutionEnvironmentSnapshot(BaseModel):
    """Persisted environment facts that are material to one reviewed plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    snapshot_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    captured_at: datetime
    provider_kind: Literal["local"] = "local"
    platform: str = Field(min_length=1, max_length=256)
    python_version: str = Field(min_length=1, max_length=256)
    app_version: str = Field(min_length=1, max_length=128)
    node_registry_hash: str = Field(min_length=1, max_length=128)
    contract_versions: tuple[tuple[str, str], ...]
    tool_capabilities: tuple[ToolCapabilitySnapshot, ...] = ()
    backend_capabilities: tuple[BackendCapabilitySnapshot, ...] = ()
    write_roots_hash: str = Field(min_length=1, max_length=128)
    readonly_roots_hash: str = Field(min_length=1, max_length=128)
    sandbox_provider: Literal["windows_restricted_process"] = "windows_restricted_process"
    sandbox_provider_version: str = "windows-sandbox-v1"
    sandbox_runtime_hash: str = Field(
        default="windows-sandbox-runtime-v1",
        min_length=1,
        max_length=128,
    )
    environment_hash: str = Field(min_length=1, max_length=128)
