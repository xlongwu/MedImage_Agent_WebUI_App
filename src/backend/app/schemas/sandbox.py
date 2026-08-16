"""Immutable contracts for gateway-owned Windows sandbox attempts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_seconds: int = Field(ge=1, le=86_400)
    memory_limit_bytes: int = Field(ge=16 * 1024 * 1024)
    max_processes: int = Field(ge=1, le=128)


class SandboxPolicy(BaseModel):
    """Redacted authority for one sandbox-process node.

    Paths are deliberately represented only by hashes.  The process runner
    resolves the verified path from the persisted environment snapshot.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policy_version: str = "windows-sandbox-v1"
    node_id: str
    backend_id: str
    provider: Literal["windows_restricted_process"]
    executable_id: str
    executable_path_hash: str
    readonly_root_hashes: tuple[str, ...]
    output_root_hashes: tuple[str, ...]
    allowed_environment_keys: tuple[str, ...]
    network_isolation: Literal["not_enforced"] = "not_enforced"
    limits: SandboxLimits
    policy_hash: str


class SandboxPolicySet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policies: tuple[SandboxPolicy, ...] = ()
    policies_hash: str


SandboxAttemptStatus = Literal[
    "PREPARING", "PREPARED", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED",
    "TIMED_OUT", "CANCELLED", "INTERRUPTED",
]


class SandboxAttemptRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    sandbox_id: str
    project_id: str
    run_id: str
    node_id: str
    subject_id: str | None = None
    attempt_id: str
    execution_ticket_id: str
    dispatch_id: str
    policy_hash: str
    status: SandboxAttemptStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    result_code: str | None = None
    command_hash: str | None = None
    output_manifest_hash: str | None = None
    output_count: int = Field(default=0, ge=0)
    owner_pid: int | None = Field(default=None, ge=1)
    provider: str = "windows_restricted_process"
    network_isolation: Literal["not_enforced"] = "not_enforced"


class SandboxProcessRequest(BaseModel):
    """Internal-only request; it must never be exposed through an API schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox_id: str
    executable_path: str
    argv: tuple[str, ...]
    cwd: str
    environment: dict[str, str]
    policy_hash: str
    timeout_seconds: int = Field(ge=1, le=86_400)
    memory_limit_bytes: int = Field(ge=16 * 1024 * 1024)
    max_processes: int = Field(ge=1, le=128)


class SandboxProcessResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox_id: str
    status: SandboxAttemptStatus
    return_code: int | None
    started_at: datetime
    ended_at: datetime
    terminated_reason: str | None = None
    stdout_path: str
    stderr_path: str
    output_manifest_hash: str | None = None
