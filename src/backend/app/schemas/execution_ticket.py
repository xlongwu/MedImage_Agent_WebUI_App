"""Server-issued execution capability bound to one reviewed plan."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ExecutionTicketStatus = Literal["issued", "consumed", "revoked", "expired"]
ExecutionTicketKind = Literal["reviewed_execution", "recovery_child"]


class ExecutionRetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_retry_count: int = Field(default=0, ge=0, le=10)
    allowed_node_ids: tuple[str, ...] = ()
    require_approval: bool = True
    max_lifecycle_recovery_attempts: int | None = Field(default=None, ge=0)
    max_node_attempts: int | None = Field(default=None, ge=0)
    max_subject_node_attempts: int | None = Field(default=None, ge=0)
    max_replans: int | None = Field(default=None, ge=0)
    max_recovery_wall_seconds: int | None = Field(default=None, ge=0)


class ExecutionTicket(BaseModel):
    """Persisted, immutable execution authority created only by the backend."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 4
    ticket_kind: ExecutionTicketKind = "reviewed_execution"
    execution_ticket_id: str
    project_id: str
    reviewed_plan_id: str
    plan_hash: str
    goal_contract_hash: str
    evaluation_policy_version: str
    approval_summary_hash: str
    execution_environment_snapshot_id: str
    execution_environment_hash: str
    execution_provider_kind: Literal["local"] = "local"
    memory_context_hash: str | None = None
    approved_actor: str
    approved_node_ids: tuple[str, ...]
    approved_backend_ids: tuple[str, ...]
    input_roots: tuple[str, ...]
    output_roots: tuple[str, ...]
    readonly_roots: tuple[str, ...] = ()
    project_config_path: str
    pipeline_path: str
    scope_hash: str
    allowlist_hash: str
    normalized_params_hash: str
    contract_versions: tuple[tuple[str, str], ...]
    audit_id: str
    issued_at: datetime
    expires_at: datetime
    retry_policy: ExecutionRetryPolicy = Field(default_factory=ExecutionRetryPolicy)
    parent_execution_ticket_id: str | None = None
    parent_ticket_hash: str | None = None
    parent_run_id: str | None = None
    recovery_approval_id: str | None = None
    recovery_proposal_id: str | None = None
    recovery_proposal_hash: str | None = None
    recovery_candidate_id: str | None = None
    recovery_candidate_hash: str | None = None
    recovery_attempt_id: str | None = None
    quota_reservation_id: str | None = None
    recovery_action: Literal["SAFE_RETRY", "RETRY_FAILED_SUBJECTS", "RESUME"] | None = None
    recovery_node_ids: tuple[str, ...] = ()
    recovery_subject_ids: tuple[str, ...] = ()
    checkpoint_id: str | None = None
    recovery_run_id: str | None = None
    output_namespace: str | None = None
    status: ExecutionTicketStatus = "issued"
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    idempotency_key: str | None = None
    canonical_hash: str

    @model_validator(mode="after")
    def validate_recovery_child_binding(self) -> ExecutionTicket:
        if self.ticket_kind != "recovery_child":
            return self
        required = (
            self.parent_execution_ticket_id,
            self.parent_ticket_hash,
            self.parent_run_id,
            self.recovery_approval_id,
            self.recovery_proposal_id,
            self.recovery_proposal_hash,
            self.recovery_candidate_id,
            self.recovery_candidate_hash,
            self.recovery_attempt_id,
            self.quota_reservation_id,
            self.recovery_action,
            self.recovery_run_id,
            self.output_namespace,
        )
        if any(not value for value in required) or not self.recovery_node_ids:
            raise ValueError("recovery child ticket requires complete immutable lineage")
        if self.execution_provider_kind != "local":
            raise ValueError("recovery child ticket cannot switch execution provider")
        if self.recovery_action == "RETRY_FAILED_SUBJECTS" and not self.recovery_subject_ids:
            raise ValueError("failed-subject recovery requires an explicit subject scope")
        if self.recovery_action == "RESUME" and not self.checkpoint_id:
            raise ValueError("resume recovery requires a checkpoint binding")
        return self

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return current >= expiry


class ExecutionTicketEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    event_id: str
    execution_ticket_id: str
    project_id: str
    event_type: str
    occurred_at: datetime
    audit_id: str | None = None
    reason: str | None = None
    details: dict[str, object] = Field(default_factory=dict)
