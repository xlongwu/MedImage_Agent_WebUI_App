"""Machine-verifiable, versioned contract for a pipeline node."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ParameterType = Literal["string", "integer", "number", "boolean", "object", "array"]
CapabilityLevel = Literal["unavailable", "scaffolded", "metadata_only", "computed", "validated"]
RiskLevel = Literal["low", "medium", "high"]


class ParameterContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ParameterType
    required: bool = False
    nullable: bool = False
    default: Any = None
    enum: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    min_items: int | None = None
    max_items: int | None = None
    description: str = ""
    path_access: Literal["read", "write", "non_path"] | None = None


class ArtifactContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_type: str
    required: bool = True
    modalities: tuple[str, ...] = ()
    reload_required: bool = False


class ResourceRequirements(BaseModel):
    model_config = ConfigDict(frozen=True)

    backend: str
    cpu_cores_min: int = 1
    gpu_required: bool = False
    process_mode: Literal["in_process", "sandbox_process"] = "in_process"


class ContractRetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    retryable: bool = False
    retryable_error_classes: tuple[str, ...] = ()
    non_retryable_error_classes: tuple[str, ...] = ()
    max_attempts: int = 0
    backoff_policy: Literal["none", "fixed"] = "none"
    backoff_seconds: int = Field(default=0, ge=0, le=3600)
    requires_approval: bool = True
    supports_subject_subset: bool = False
    supports_resume: bool = False
    checkpoint_schema: str | None = None
    mutable_parameters_for_recovery: tuple[str, ...] = ()
    backend_switch_targets: tuple[str, ...] = ()
    backend_scientific_equivalence: dict[str, str] = Field(default_factory=dict)
    required_pre_retry_validations: tuple[str, ...] = ()
    required_post_retry_validations: tuple[str, ...] = ()
    max_lifecycle_recovery_attempts: int | None = Field(default=None, ge=0)
    max_node_attempts: int | None = Field(default=None, ge=0)
    max_subject_node_attempts: int | None = Field(default=None, ge=0)
    max_replans: int | None = Field(default=None, ge=0)
    max_recovery_wall_seconds: int | None = Field(default=None, ge=0)


class IdempotencyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    idempotent: bool = False
    key_fields: tuple[str, ...] = ()
    overwrite_policy: str = "fail_if_exists"
    output_collision_policy: Literal[
        "fail_if_exists",
        "reuse_verified",
        "isolated_attempt",
    ] = "fail_if_exists"
    attempt_output_strategy: Literal[
        "none",
        "isolated_subdirectory",
        "atomic_replace",
    ] = "none"


class ValidationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    allow_additional_parameters: bool = False
    enforce_backend: bool = True
    compatibility_mode: str | None = None
    deprecation: str | None = None


class NodeContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    node_id: str
    contract_version: str
    backend: str
    parallel_level: Literal["project", "subject"] = "project"
    requires_approval: bool
    manual_required: bool
    risk_level: RiskLevel
    write_roots: tuple[str, ...] = ()
    input_schema: tuple[ArtifactContract, ...] = ()
    parameter_schema: dict[str, ParameterContract] = Field(default_factory=dict)
    output_schema: tuple[ArtifactContract, ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    resources: ResourceRequirements
    retry_policy: ContractRetryPolicy = Field(default_factory=ContractRetryPolicy)
    idempotency: IdempotencyPolicy = Field(default_factory=IdempotencyPolicy)
    validation_policy: ValidationPolicy = Field(default_factory=ValidationPolicy)
    capability_level: CapabilityLevel = "unavailable"
    executable: bool = False


class ContractValidationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    contract_version: str
    normalized_parameters: dict[str, Any]
    normalized_parameters_hash: str
    checks: tuple[str, ...]
