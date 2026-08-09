"""Execution Consistency Schema — Phase 3 Productization.

Defines consistency status/issue types, consistency input models,
consistency report model, and pure helper functions for verifying
that a reviewed plan, dry-run manifest, and execution request agree
on all invariants before execution proceeds.

Schema-only module.  No runtime executor is imported or modified.
No file I/O.  No external-tool execution is enabled.

Reference:
  docs/规划与运行时/流水线执行器产品化契约.md  (Section 5)
  docs/规划与运行时/运行重试与恢复契约.md
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════════
# 1. Literal type aliases
# ═══════════════════════════════════════════════════════════════════════

ConsistencyStatus = Literal[
    "pass",
    "warning",
    "fail",
    "unknown",
]

ConsistencyIssueSeverity = Literal[
    "info",
    "warning",
    "error",
]

ConsistencyIssueCode = Literal[
    "PLAN_HASH_MISMATCH",
    "REVIEWED_PLAN_ID_MISMATCH",
    "PROJECT_ID_MISMATCH",
    "PROJECT_CONFIG_PATH_MISMATCH",
    "PROJECT_CONTEXT_PATH_MISSING",
    "NODE_SET_MISMATCH",
    "NODE_PARAM_HASH_MISMATCH",
    "OUTPUT_ROOT_MISMATCH",
    "OUTPUT_MANIFEST_MISSING",
    "SAFE_ALLOWLIST_CHANGED",
    "APPROVAL_CONTEXT_MISSING",
    "AUDIT_CONTEXT_MISSING",
    "DRY_RUN_STATUS_NOT_READY",
    "UNKNOWN",
]

# Accepted dry-run status values that permit execution to proceed.
_ACCEPTED_DRY_RUN_STATUSES: frozenset[str] = frozenset(
    {
        "ready",
        "DRY_RUN_OK",
        "EXECUTION_PREFLIGHT_READY",
    }
)


# ═══════════════════════════════════════════════════════════════════════
# 2. Models
# ═══════════════════════════════════════════════════════════════════════


class ConsistencyIssue(BaseModel):
    """A single consistency violation found during verification."""

    severity: ConsistencyIssueSeverity = "error"
    code: ConsistencyIssueCode = "UNKNOWN"
    message: str
    field: str | None = None
    expected: Any | None = None
    actual: Any | None = None
    node_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ExecutionConsistencyInput(BaseModel):
    """Snapshot of the key fields from a reviewed plan, dry-run manifest,
    or execution request that must be consistent across all three.

    Every field is optional *at the model level* so that partial inputs
    can be constructed for testing, but the verifier will flag missing
    mandatory fields as issues.
    """

    project_id: str = ""
    reviewed_plan_id: str | None = None
    plan_hash: str | None = None
    project_config_path: str | None = None
    project_context_path: str | None = None
    node_ids: list[str] = Field(default_factory=list)
    node_param_hashes: dict[str, str] = Field(default_factory=dict)
    output_root: str | None = None
    output_manifest_ids: list[str] = Field(default_factory=list)
    allowlist_hash: str | None = None
    approval_summary_hash: str | None = None
    audit_id: str | None = None
    dry_run_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionConsistencyReport(BaseModel):
    """Result of calling ``verify_execution_consistency()``.

    ``ok`` is True only when ``status`` is ``"pass"``.
    """

    ok: bool
    status: ConsistencyStatus
    issue_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    issues: list[ConsistencyIssue] = Field(default_factory=list)
    checked_fields: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# 3. Pure helper functions
# ═══════════════════════════════════════════════════════════════════════


def summarize_consistency_issues(
    issues: list[ConsistencyIssue],
) -> dict[str, int]:
    """Aggregate issue counts by severity.

    Returns dict with keys:
      issue_count, error_count, warning_count, info_count
    """
    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    info_count = sum(1 for i in issues if i.severity == "info")
    return {
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
    }


def _issue(
    code: ConsistencyIssueCode,
    message: str,
    *,
    severity: ConsistencyIssueSeverity = "error",
    field: str | None = None,
    expected: Any | None = None,
    actual: Any | None = None,
    node_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> ConsistencyIssue:
    """Factory for ConsistencyIssue — keeps the verifier body readable."""
    return ConsistencyIssue(
        severity=severity,
        code=code,
        message=message,
        field=field,
        expected=expected,
        actual=actual,
        node_id=node_id,
        details=details or {},
    )


def verify_execution_consistency(
    *,
    reviewed: ExecutionConsistencyInput,
    dry_run: ExecutionConsistencyInput,
    execution: ExecutionConsistencyInput,
    require_approval: bool = True,
    require_audit: bool = True,
    require_output_manifest: bool = True,
) -> ExecutionConsistencyReport:
    """Compare reviewed / dry_run / execution inputs for invariant violations.

    Returns an ``ExecutionConsistencyReport`` whose ``ok`` is True only
    when ``status`` is ``"pass"``.

    Pure function — no filesystem checks, no file I/O, no path resolution,
    no subprocess inspection.  Only compares the string/dict/list fields
    provided in the three input snapshots.
    """
    issues: list[ConsistencyIssue] = []
    checked: list[str] = []

    # ── 1. project_id ──────────────────────────────────────────────────
    checked.append("project_id")
    _ids = {
        "reviewed": reviewed.project_id,
        "dry_run": dry_run.project_id,
        "execution": execution.project_id,
    }
    if reviewed.project_id and dry_run.project_id and reviewed.project_id != dry_run.project_id:
        issues.append(
            _issue(
                "PROJECT_ID_MISMATCH",
                f"reviewed project_id '{reviewed.project_id}' != dry_run project_id '{dry_run.project_id}'",
                field="project_id",
                expected=reviewed.project_id,
                actual=dry_run.project_id,
            )
        )
    if dry_run.project_id and execution.project_id and dry_run.project_id != execution.project_id:
        issues.append(
            _issue(
                "PROJECT_ID_MISMATCH",
                f"dry_run project_id '{dry_run.project_id}' != execution project_id '{execution.project_id}'",
                field="project_id",
                expected=dry_run.project_id,
                actual=execution.project_id,
            )
        )
    if reviewed.project_id and execution.project_id and reviewed.project_id != execution.project_id:
        issues.append(
            _issue(
                "PROJECT_ID_MISMATCH",
                f"reviewed project_id '{reviewed.project_id}' != execution project_id '{execution.project_id}'",
                field="project_id",
                expected=reviewed.project_id,
                actual=execution.project_id,
            )
        )

    # ── 2. reviewed_plan_id ────────────────────────────────────────────
    checked.append("reviewed_plan_id")
    for label_a, inp_a in [("reviewed", reviewed), ("dry_run", dry_run), ("execution", execution)]:
        for label_b, inp_b in [
            ("reviewed", reviewed),
            ("dry_run", dry_run),
            ("execution", execution),
        ]:
            if label_a >= label_b:
                continue
            if (
                inp_a.reviewed_plan_id is not None
                and inp_b.reviewed_plan_id is not None
                and inp_a.reviewed_plan_id != inp_b.reviewed_plan_id
            ):
                issues.append(
                    _issue(
                        "REVIEWED_PLAN_ID_MISMATCH",
                        f"{label_a} reviewed_plan_id '{inp_a.reviewed_plan_id}' != {label_b} reviewed_plan_id '{inp_b.reviewed_plan_id}'",
                        field="reviewed_plan_id",
                        expected=inp_a.reviewed_plan_id,
                        actual=inp_b.reviewed_plan_id,
                    )
                )

    # ── 3. plan_hash ───────────────────────────────────────────────────
    checked.append("plan_hash")
    for label_a, inp_a in [("reviewed", reviewed), ("dry_run", dry_run), ("execution", execution)]:
        for label_b, inp_b in [
            ("reviewed", reviewed),
            ("dry_run", dry_run),
            ("execution", execution),
        ]:
            if label_a >= label_b:
                continue
            if (
                inp_a.plan_hash is not None
                and inp_b.plan_hash is not None
                and inp_a.plan_hash != inp_b.plan_hash
            ):
                issues.append(
                    _issue(
                        "PLAN_HASH_MISMATCH",
                        f"{label_a} plan_hash '{inp_a.plan_hash}' != {label_b} plan_hash '{inp_b.plan_hash}'",
                        field="plan_hash",
                        expected=inp_a.plan_hash,
                        actual=inp_b.plan_hash,
                    )
                )

    # ── 4. project_config_path ─────────────────────────────────────────
    checked.append("project_config_path")
    if (
        dry_run.project_config_path is not None
        and execution.project_config_path is not None
        and dry_run.project_config_path != execution.project_config_path
    ):
        issues.append(
            _issue(
                "PROJECT_CONFIG_PATH_MISMATCH",
                f"dry_run project_config_path '{dry_run.project_config_path}' != execution '{execution.project_config_path}'",
                field="project_config_path",
                expected=dry_run.project_config_path,
                actual=execution.project_config_path,
            )
        )

    # ── 5. project_context_path on execution ───────────────────────────
    checked.append("project_context_path")
    if not execution.project_context_path:
        issues.append(
            _issue(
                "PROJECT_CONTEXT_PATH_MISSING",
                "execution is missing project_context_path",
                field="project_context_path",
            )
        )

    # ── 6. node_ids match as sets ──────────────────────────────────────
    checked.append("node_ids")
    dr_nodes = set(dry_run.node_ids)
    ex_nodes = set(execution.node_ids)
    if dr_nodes and ex_nodes and dr_nodes != ex_nodes:
        only_dr = sorted(dr_nodes - ex_nodes)
        only_ex = sorted(ex_nodes - dr_nodes)
        issues.append(
            _issue(
                "NODE_SET_MISMATCH",
                f"dry_run nodes {sorted(dr_nodes)} != execution nodes {sorted(ex_nodes)}",
                field="node_ids",
                expected=sorted(dr_nodes),
                actual=sorted(ex_nodes),
                details={"only_in_dry_run": only_dr, "only_in_execution": only_ex},
            )
        )

    # ── 7. node_param_hashes match for common nodes ────────────────────
    checked.append("node_param_hashes")
    common = set(dry_run.node_param_hashes.keys()) & set(execution.node_param_hashes.keys())
    for nid in sorted(common):
        dr_val = dry_run.node_param_hashes[nid]
        ex_val = execution.node_param_hashes[nid]
        if dr_val != ex_val:
            issues.append(
                _issue(
                    "NODE_PARAM_HASH_MISMATCH",
                    f"node '{nid}': dry_run param hash '{dr_val}' != execution param hash '{ex_val}'",
                    field="node_param_hashes",
                    node_id=nid,
                    expected=dr_val,
                    actual=ex_val,
                )
            )

    # ── 8. output_root ─────────────────────────────────────────────────
    checked.append("output_root")
    if (
        dry_run.output_root is not None
        and execution.output_root is not None
        and dry_run.output_root != execution.output_root
    ):
        issues.append(
            _issue(
                "OUTPUT_ROOT_MISMATCH",
                f"dry_run output_root '{dry_run.output_root}' != execution output_root '{execution.output_root}'",
                field="output_root",
                expected=dry_run.output_root,
                actual=execution.output_root,
            )
        )

    # ── 9. output_manifest_ids ─────────────────────────────────────────
    checked.append("output_manifest_ids")
    if require_output_manifest and not execution.output_manifest_ids:
        issues.append(
            _issue(
                "OUTPUT_MANIFEST_MISSING",
                "execution is missing output_manifest_ids",
                field="output_manifest_ids",
            )
        )

    # ── 10. allowlist_hash ─────────────────────────────────
    checked.append("allowlist_hash")
    if (
        dry_run.allowlist_hash is not None
        and execution.allowlist_hash is not None
        and dry_run.allowlist_hash != execution.allowlist_hash
    ):
        issues.append(
            _issue(
                "SAFE_ALLOWLIST_CHANGED",
                f"safe allowlist fingerprint changed: dry_run "
                f"'{dry_run.allowlist_hash}' != execution "
                f"'{execution.allowlist_hash}'",
                field="allowlist_hash",
                expected=dry_run.allowlist_hash,
                actual=execution.allowlist_hash,
            )
        )

    # ── 11. approval_summary_hash ────────────────────────────────────────
    checked.append("approval_summary_hash")
    if require_approval and not execution.approval_summary_hash:
        issues.append(
            _issue(
                "APPROVAL_CONTEXT_MISSING",
                "execution is missing approval_summary_hash (required)",
                field="approval_summary_hash",
            )
        )

    # ── 12. audit_id ───────────────────────────────────────────────────
    checked.append("audit_id")
    if require_audit and not execution.audit_id:
        issues.append(
            _issue(
                "AUDIT_CONTEXT_MISSING",
                "execution is missing audit_id (required)",
                field="audit_id",
            )
        )

    # ── 13. dry_run_status ─────────────────────────────────────────────
    checked.append("dry_run_status")
    dr_status = dry_run.dry_run_status
    if dr_status is not None and dr_status not in _ACCEPTED_DRY_RUN_STATUSES:
        issues.append(
            _issue(
                "DRY_RUN_STATUS_NOT_READY",
                f"dry_run_status '{dr_status}' is not an accepted ready status. "
                f"Must be one of: {sorted(_ACCEPTED_DRY_RUN_STATUSES)}",
                field="dry_run_status",
                expected=f"one of {sorted(_ACCEPTED_DRY_RUN_STATUSES)}",
                actual=dr_status,
            )
        )

    # ── Compute overall status ─────────────────────────────────────────
    summary = summarize_consistency_issues(issues)

    if summary["error_count"] > 0:
        status: ConsistencyStatus = "fail"
    elif summary["warning_count"] > 0:
        status = "warning"
    elif summary["issue_count"] == 0:
        status = "pass"
    else:
        # Only info-level issues exist — treat as warning since they
        # represent non-zero discrepancies.
        status = "warning"

    return ExecutionConsistencyReport(
        ok=(status == "pass"),
        status=status,
        issue_count=summary["issue_count"],
        error_count=summary["error_count"],
        warning_count=summary["warning_count"],
        issues=issues,
        checked_fields=checked,
    )
