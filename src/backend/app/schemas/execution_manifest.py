"""Execution Manifest Schema — Phase 3 Productization.

Defines output manifests, output manifest items, output verification metadata,
execution provenance, and failure records for the productized pipeline executor.

Schema-only module.  No runtime executor is imported or modified.
No file I/O.  No external-tool execution is enabled.

Reference:
  docs/规划与运行时/流水线执行器产品化契约.md
  docs/规划与运行时/运行重试与恢复契约.md
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ═══════════════════════════════════════════════════════════════════════
# 1. Literal type aliases
# ═══════════════════════════════════════════════════════════════════════

OutputVerificationStatus = Literal[
    "not_checked",
    "verified",
    "missing",
    "checksum_mismatch",
    "size_mismatch",
    "unreadable",
    "unsafe_path",
    "unknown",
]

OutputArtifactKind = Literal[
    "nifti",
    "json",
    "markdown",
    "csv",
    "tsv",
    "log",
    "stdout_log",
    "stderr_log",
    "provenance_json",
    "node_state_json",
    "report",
    "thumbnail",
    "directory",
    "other",
]

ExecutionBackend = Literal[
    "python",
    "contract",
    "matlab-spm",
    "matlab-dpabi",
    "external",
    "unknown",
]

ExecutionFailureStage = Literal[
    "preflight",
    "approval",
    "audit",
    "execution",
    "timeout",
    "output_verification",
    "artifact_discovery",
    "provenance",
    "unknown",
]

# ═══════════════════════════════════════════════════════════════════════
# 2. Output Manifest Item
# ═══════════════════════════════════════════════════════════════════════

class OutputManifestItem(BaseModel):
    """A single expected or actual output of a pipeline node."""

    kind: OutputArtifactKind = "other"
    path: str
    relative_path: str | None = None
    required: bool = True
    exists: bool = False
    verified: bool = False
    verification_status: OutputVerificationStatus = "not_checked"
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    modified_at: str | None = None
    previewable: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _verified_implies_exists(self) -> OutputManifestItem:
        if self.verified and not self.exists:
            raise ValueError("verified=True requires exists=True")
        if self.verification_status == "verified" and not self.exists:
            raise ValueError(
                "verification_status='verified' requires exists=True"
            )
        return self

    @model_validator(mode="after")
    def _path_not_empty(self) -> OutputManifestItem:
        if not self.path.strip():
            raise ValueError("path must be non-empty")
        return self

    @model_validator(mode="after")
    def _size_bytes_non_negative(self) -> OutputManifestItem:
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        return self


# ═══════════════════════════════════════════════════════════════════════
# 3. Output Manifest
# ═══════════════════════════════════════════════════════════════════════

class OutputManifest(BaseModel):
    """Aggregated output manifest for one node execution."""

    project_id: str
    run_id: str
    node_id: str
    subject_id: str | None = None
    session_id: str | None = None
    output_root: str | None = None
    items: list[OutputManifestItem] = Field(default_factory=list)
    missing_required_count: int = 0
    verified_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# 4. Execution Provenance
# ═══════════════════════════════════════════════════════════════════════

class ExecutionProvenance(BaseModel):
    """Provenance record for one node execution.

    Records inputs, outputs, parameters, software versions, environment
    fingerprint, and approval/audit context.  No shell command field.
    ``command_template_id`` is an identifier only, not executable code.
    """

    model_config = {"extra": "forbid"}

    project_id: str
    reviewed_plan_id: str | None = None
    execution_ticket_id: str | None = None
    dispatch_id: str | None = None
    approval_summary_hash: str | None = None
    plan_hash: str | None = None
    memory_context_hash: str | None = None
    scope_hash: str | None = None
    allowlist_hash: str | None = None
    run_id: str
    node_id: str
    backend: ExecutionBackend = "unknown"
    command_template_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    parameter_hash: str | None = None
    input_paths: list[str] = Field(default_factory=list)
    input_checksums: dict[str, str] = Field(default_factory=dict)
    output_paths: list[str] = Field(default_factory=list)
    output_checksums: dict[str, str] = Field(default_factory=dict)
    software_versions: dict[str, str] = Field(default_factory=dict)
    environment_fingerprint: str | None = None
    approval_context: dict[str, Any] | None = None
    audit_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# 5. Execution Failure Record
# ═══════════════════════════════════════════════════════════════════════

class ExecutionFailureRecord(BaseModel):
    """Record of an execution failure at a specific stage."""

    model_config = {"extra": "forbid"}

    stage: ExecutionFailureStage = "unknown"
    status: str = "failed"
    message: str
    node_id: str | None = None
    retryable: bool = False
    resume_eligible: bool = False
    next_action: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

# ═══════════════════════════════════════════════════════════════════════
# 6. Pure helper functions
# ═══════════════════════════════════════════════════════════════════════

def count_missing_required(items: list[OutputManifestItem]) -> int:
    """Return count of required items that do not exist."""
    return sum(1 for item in items if item.required and not item.exists)


def count_verified(items: list[OutputManifestItem]) -> int:
    """Return count of verified items."""
    return sum(1 for item in items if item.verified)


def count_manifest_warnings(items: list[OutputManifestItem]) -> int:
    """Return total warning count across all items."""
    return sum(len(item.warnings) for item in items)


def count_manifest_errors(items: list[OutputManifestItem]) -> int:
    """Return total error count across all items."""
    return sum(len(item.errors) for item in items)


def count_previewable(items: list[OutputManifestItem]) -> int:
    """Return count of previewable items."""
    return sum(1 for item in items if item.previewable)


def summarize_output_manifest(items: list[OutputManifestItem]) -> dict[str, int]:
    """Aggregate summary counts for a list of manifest items.

    Returns dict with keys:
      total_count, required_count, missing_required_count, verified_count,
      warning_count, error_count, previewable_count
    """
    return {
        "total_count": len(items),
        "required_count": sum(1 for i in items if i.required),
        "missing_required_count": count_missing_required(items),
        "verified_count": count_verified(items),
        "warning_count": count_manifest_warnings(items),
        "error_count": count_manifest_errors(items),
        "previewable_count": count_previewable(items),
    }


def build_output_manifest(
    *,
    project_id: str,
    run_id: str,
    node_id: str,
    items: list[OutputManifestItem],
    subject_id: str | None = None,
    session_id: str | None = None,
    output_root: str | None = None,
) -> OutputManifest:
    """Build an output manifest with auto-computed summary counts.

    Pure function — no filesystem checks, no file I/O, no path resolution.
    """
    summary = summarize_output_manifest(items)
    return OutputManifest(
        project_id=project_id,
        run_id=run_id,
        node_id=node_id,
        subject_id=subject_id,
        session_id=session_id,
        output_root=output_root,
        items=items,
        missing_required_count=summary["missing_required_count"],
        verified_count=summary["verified_count"],
        warning_count=summary["warning_count"],
        error_count=summary["error_count"],
    )
