"""Immutable evidence snapshots for one controlled pipeline run.

Observation records deliberately separate facts collected from runtime/state
sources from later goal evaluation and recovery decisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.backend.app.schemas.node_contract import CapabilityLevel

SourceReadStatus = Literal["ok", "missing", "invalid", "rejected"]
FreshnessStatus = Literal["fresh", "stale", "unknown"]
CompletenessStatus = Literal["complete", "partial", "invalid"]
ReloadStatus = Literal["passed", "failed", "not_required", "unknown"]


class ObservationBindings(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    lifecycle_id: str
    reviewed_plan_id: str
    plan_hash: str
    goal_contract_id: str | None = None
    goal_contract_hash: str | None = None
    run_id: str
    execution_ticket_id: str
    dispatch_id: str
    recovery_attempt_id: str | None = None


class ObservationSourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    source_type: str
    record_id: str | None = None
    relative_path: str | None = None
    content_hash: str | None = None
    read_status: SourceReadStatus
    observed_at: datetime
    modified_at: datetime | None = None
    freshness: FreshnessStatus = "unknown"
    warnings: tuple[str, ...] = ()
    redacted: bool = True


class PipelineObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = "UNKNOWN"
    pipeline_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    nodes_total: int | None = None
    nodes_succeeded: int | None = None
    nodes_failed: int | None = None
    nodes_skipped: int | None = None
    active_nodes: int | None = None
    summary_consistent: bool = False
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class NodeObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    subject_id: str = "project"
    session_id: str | None = None
    status: str = "UNKNOWN"
    attempt: int = 0
    backend: str | None = None
    contract_version: str | None = None
    outputs: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class ArtifactObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str
    artifact_type: str
    owner_node_id: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    relative_path: str | None = None
    exists: bool = False
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    input_hashes: tuple[str, ...] = ()
    parameter_hash: str | None = None
    shape: tuple[int, ...] = ()
    dtype: str | None = None
    reload_status: ReloadStatus = "unknown"
    reload_message: str | None = None
    provenance_id: str | None = None
    registration_status: Literal["registered", "unregistered", "unknown"] = "unknown"
    limitation_flags: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class ValidationObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    validation_id: str
    validator_id: str
    validator_version: str
    scope: str
    status: Literal["passed", "failed", "warning", "unknown"]
    checks: tuple[str, ...] = ()
    blocking_issues: tuple[str, ...] = ()
    report_ref: str | None = None
    report_hash: str | None = None
    evidence_ids: tuple[str, ...] = ()


class ObservationLogFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    level: Literal["warning", "error"]
    source_id: str
    code: str | None = None
    message: str
    node_id: str | None = None
    subject_id: str | None = None
    redaction_flags: tuple[str, ...] = ()


class CapabilityObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    declared_level: CapabilityLevel = "unavailable"
    observed_level: CapabilityLevel = "unavailable"
    defensible_level: CapabilityLevel = "unavailable"
    downgrade_reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class ScientificObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CapabilityLevel = "unavailable"
    limitation_flags: tuple[str, ...] = ()
    backend_ids: tuple[str, ...] = ()
    validation_evidence_ids: tuple[str, ...] = ()


class ObservationCompleteness(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CompletenessStatus
    missing_sources: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    blocking_facts: tuple[str, ...] = ()


class ObservationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    observation_id: str
    observation_hash: str
    completeness: CompletenessStatus
    execution_status: str
    capability_level: CapabilityLevel
    scientific_status: CapabilityLevel
    limitation_flags: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ObservationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    observation_id: str
    schema_version: int = 1
    collector_version: str = "observation-collector-v1"
    bindings: ObservationBindings
    collected_at: datetime
    sources: tuple[ObservationSourceRef, ...]
    pipeline: PipelineObservation
    nodes: tuple[NodeObservation, ...] = ()
    artifacts: tuple[ArtifactObservation, ...] = ()
    validations: tuple[ValidationObservation, ...] = ()
    log_facts: tuple[ObservationLogFact, ...] = ()
    capability: CapabilityObservation
    scientific: ScientificObservation
    completeness: ObservationCompleteness
    previous_observation_id: str | None = None
    observation_hash: str
    extensions: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> ObservationSummary:
        warnings = tuple(
            dict.fromkeys(
                warning
                for source in self.sources
                for warning in source.warnings
            )
        )
        return ObservationSummary(
            observation_id=self.observation_id,
            observation_hash=self.observation_hash,
            completeness=self.completeness.status,
            execution_status=self.pipeline.status,
            capability_level=self.capability.defensible_level,
            scientific_status=self.scientific.status,
            limitation_flags=self.scientific.limitation_flags,
            warnings=warnings,
        )
