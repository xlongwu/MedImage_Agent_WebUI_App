"""Approval Gate — validates approval records before pipeline execution.

The Approval Gate sits between Plan Validator and Pipeline Executor.
It checks that: validation passed, required approvals are granted,
no nodes are rejected, manual-required nodes are not yet executed, and
high-risk backends (MATLAB/SPM/DPABI) have explicit approval.

M6-T003: node-level + backend-level approval for high-risk backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── High-risk backends ──────────────────────────────────────────────────────

HIGH_RISK_BACKENDS: frozenset[str] = frozenset({
    "matlab-spm",
    "matlab-dpabi",
    "dpabi",
    "matlab",
})


def _high_risk_node_ids_from_plan(plan: dict[str, Any]) -> set[str]:
    """Return node ids in the plan whose backend is high-risk."""
    nodes = plan.get("nodes", []) or []
    result: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        backend = node.get("backend", "")
        if nid and backend in HIGH_RISK_BACKENDS:
            result.add(str(nid))
    return result


def _native_full_execute_node_ids_from_plan(plan: dict[str, Any]) -> set[str]:
    nodes = plan.get("nodes", []) or []
    result: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("id") == "native_preproc_full_execute":
            result.add("native_preproc_full_execute")
    return result


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ApprovalRecord:
    """A human approval record for a pipeline plan."""

    approved: bool
    approved_by: str | None = None
    approved_at: str | None = None
    reason: str | None = None
    approved_nodes: list[str] | None = None
    rejected_nodes: list[str] | None = None
    review_draft_schema_version: str | None = None
    approved_backends: list[str] | None = None  # M6-T003
    # M6-T004: external-tool safety acknowledgement fields
    external_tool_acknowledgement: bool | None = None
    rawdata_read_only_confirmed: bool | None = None
    output_directory_confirmed: bool | None = None
    risk_acknowledgement: bool | None = None
    overwrite_policy: str | None = None
    subject_scope_confirmed: bool | None = None
    native_preprocessing_acknowledgement: bool | None = None
    no_external_tools_confirmed: bool | None = None


@dataclass(frozen=True)
class ApprovalGateIssue:
    """A single issue found during approval gate checking."""

    code: str
    message: str
    node_id: str | None = None
    severity: str = "error"  # "error" | "warning"


@dataclass(frozen=True)
class ApprovalGateResult:
    """Result of checking the approval gate."""

    ok: bool
    execution_allowed: bool
    approval_required: bool
    approved: bool
    missing_approval_nodes: list[str] = field(default_factory=list)
    rejected_nodes: list[str] = field(default_factory=list)
    errors: list[ApprovalGateIssue] = field(default_factory=list)
    warnings: list[ApprovalGateIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "execution_allowed": self.execution_allowed,
            "approval_required": self.approval_required,
            "approved": self.approved,
            "missing_approval_nodes": self.missing_approval_nodes,
            "rejected_nodes": self.rejected_nodes,
            "errors": [
                {"code": e.code, "message": e.message,
                 "node_id": e.node_id, "severity": e.severity}
                for e in self.errors
            ],
            "warnings": [
                {"code": w.code, "message": w.message,
                 "node_id": w.node_id, "severity": w.severity}
                for w in self.warnings
            ],
        }


# ── Public API ───────────────────────────────────────────────────────────────

def check_approval_gate(
    plan: dict[str, Any],
    validation: dict[str, Any],
    approval: ApprovalRecord | dict[str, Any] | None,
) -> ApprovalGateResult:
    """Check whether a plan may proceed to execution given its validation
    and approval state.

    M6-T003: high-risk backends (matlab-spm, dpabi, etc.) require both
    explicit node approval AND explicit backend approval.  Wildcard
    approved_nodes=["*"] does not cover high-risk backend nodes.
    """
    errors: list[ApprovalGateIssue] = []
    warnings: list[ApprovalGateIssue] = []

    # ── 1. Validation must exist and pass ──
    if not isinstance(validation, dict):
        return ApprovalGateResult(
            ok=False, execution_allowed=False,
            approval_required=False, approved=False,
            errors=[ApprovalGateIssue("VALIDATION_MISSING", "Validation result is missing or not a dict.")],
        )

    if validation.get("ok") is not True:
        return ApprovalGateResult(
            ok=False, execution_allowed=False,
            approval_required=False, approved=False,
            errors=[ApprovalGateIssue("VALIDATION_NOT_OK", "Plan validation did not pass.")],
        )

    # ── 2. Determine if approval is required ──
    approval_required_nodes: list[str] = list(validation.get("approval_required_nodes", []) or [])
    high_risk_nodes: list[str] = list(validation.get("high_risk_nodes", []) or [])
    manual_required_nodes: list[str] = list(validation.get("manual_required_nodes", []) or [])
    risk_summary = validation.get("risk_summary", {}) or {}
    approval_required = bool(
        approval_required_nodes
        or high_risk_nodes
        or manual_required_nodes
        or risk_summary.get("requires_approval")
    )

    # ── 3. Identify high-risk backend nodes from plan ──
    high_risk_backend_nodes = _high_risk_node_ids_from_plan(plan)

    # ── 4. No approval needed → green light ──
    if not approval_required:
        return ApprovalGateResult(
            ok=True, execution_allowed=True,
            approval_required=False, approved=False,
        )

    # ── 5. Approval required but no approval record ──
    if approval is None:
        return ApprovalGateResult(
            ok=False, execution_allowed=False,
            approval_required=True, approved=False,
            missing_approval_nodes=list(approval_required_nodes),
            errors=[ApprovalGateIssue("APPROVAL_MISSING", "Plan requires approval but no approval record provided.")],
        )

    # Normalize approval to dict if needed
    if isinstance(approval, ApprovalRecord):
        appr_dict: dict[str, Any] = {
            "approved": approval.approved,
            "approved_nodes": approval.approved_nodes,
            "rejected_nodes": approval.rejected_nodes,
            "approved_backends": approval.approved_backends,
            "external_tool_acknowledgement": approval.external_tool_acknowledgement,
            "rawdata_read_only_confirmed": approval.rawdata_read_only_confirmed,
            "output_directory_confirmed": approval.output_directory_confirmed,
            "risk_acknowledgement": approval.risk_acknowledgement,
            "overwrite_policy": approval.overwrite_policy,
            "subject_scope_confirmed": approval.subject_scope_confirmed,
            "native_preprocessing_acknowledgement": approval.native_preprocessing_acknowledgement,
            "no_external_tools_confirmed": approval.no_external_tools_confirmed,
        }
    else:
        appr_dict = approval

    # ── 6. approved must be True ──
    if appr_dict.get("approved") is not True:
        return ApprovalGateResult(
            ok=False, execution_allowed=False,
            approval_required=True, approved=False,
            missing_approval_nodes=list(approval_required_nodes),
            errors=[ApprovalGateIssue("APPROVAL_NOT_GRANTED", "Approval record exists but 'approved' is not true.")],
        )

    approved_nodes: list[str] = list(appr_dict.get("approved_nodes") or [])
    rejected_nodes: list[str] = list(appr_dict.get("rejected_nodes") or [])
    approved_backends: list[str] = list(appr_dict.get("approved_backends") or [])
    is_wildcard = "*" in approved_nodes

    # ── 7. rejected nodes block execution ──
    if rejected_nodes:
        errors.append(ApprovalGateIssue(
            "APPROVAL_REJECTED_NODE",
            f"Plan contains rejected nodes: {', '.join(rejected_nodes)}",
        ))
        return ApprovalGateResult(
            ok=False, execution_allowed=False,
            approval_required=True, approved=True,
            rejected_nodes=rejected_nodes,
            missing_approval_nodes=list(approval_required_nodes),
            errors=errors,
        )

    # ── 8. M6-T003: High-risk backend nodes require explicit approval ──
    if high_risk_backend_nodes and is_wildcard:
        errors.append(ApprovalGateIssue(
            "WILDCARD_APPROVAL_NOT_ALLOWED_FOR_HIGH_RISK_BACKEND",
            f"Wildcard approval '[*]' cannot cover high-risk backend nodes: "
            f"{', '.join(sorted(high_risk_backend_nodes))}. "
            f"Add them to approved_nodes and include approved_backends.",
        ))
        return ApprovalGateResult(
            ok=False, execution_allowed=False,
            approval_required=True, approved=True,
            missing_approval_nodes=list(approval_required_nodes),
            errors=errors,
        )

    # ── 9. M6-T003: High-risk backend nodes must be explicitly listed ──
    if high_risk_backend_nodes and not is_wildcard:
        missing_hr = [n for n in high_risk_backend_nodes if n not in approved_nodes]
        if missing_hr:
            errors.append(ApprovalGateIssue(
                "HIGH_RISK_NODE_REQUIRES_EXPLICIT_APPROVAL",
                f"High-risk backend nodes must be explicitly approved: "
                f"{', '.join(missing_hr)}",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

    # ── 10. M6-T003: High-risk backends require approved_backends ──
    if high_risk_backend_nodes:
        # Determine which backends are used by high-risk nodes
        nodes = plan.get("nodes", []) or []
        needed_backends: set[str] = set()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nid = node.get("id")
            backend = str(node.get("backend", ""))
            if nid and str(nid) in high_risk_backend_nodes and backend in HIGH_RISK_BACKENDS:
                needed_backends.add(backend)

        missing_backends = needed_backends - set(approved_backends)
        if missing_backends:
            errors.append(ApprovalGateIssue(
                "HIGH_RISK_BACKEND_REQUIRES_APPROVAL",
                f"High-risk backends must be listed in approved_backends: "
                f"{', '.join(sorted(missing_backends))}",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

    # ── 11. approved_nodes must cover required nodes ──
    if not is_wildcard:
        missing = [n for n in approval_required_nodes if n not in approved_nodes]
        if missing:
            errors.append(ApprovalGateIssue(
                "APPROVAL_NODE_MISSING",
                f"Required nodes not individually approved: {', '.join(missing)}",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=missing,
                errors=errors,
            )

    # ── 12. Native full preprocessing requires explicit safety acknowledgements ──
    native_full_execute_nodes = _native_full_execute_node_ids_from_plan(plan)
    if native_full_execute_nodes:
        native_ack = appr_dict.get("native_preprocessing_acknowledgement")
        if native_ack is not True:
            errors.append(ApprovalGateIssue(
                "NATIVE_PREPROC_ACKNOWLEDGEMENT_REQUIRED",
                "native_preproc_full_execute requires native_preprocessing_acknowledgement=true.",
            ))
            return ApprovalGateResult(
                ok=False,
                execution_allowed=False,
                approval_required=True,
                approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        no_external_tools = appr_dict.get("no_external_tools_confirmed")
        if no_external_tools is not True:
            errors.append(ApprovalGateIssue(
                "NATIVE_PREPROC_NO_EXTERNAL_TOOLS_CONFIRMATION_REQUIRED",
                "native_preproc_full_execute requires no_external_tools_confirmed=true.",
            ))
            return ApprovalGateResult(
                ok=False,
                execution_allowed=False,
                approval_required=True,
                approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        rawdata_confirm = appr_dict.get("rawdata_read_only_confirmed")
        if rawdata_confirm is not True:
            errors.append(ApprovalGateIssue(
                "NATIVE_PREPROC_RAWDATA_READ_ONLY_CONFIRMATION_REQUIRED",
                "native_preproc_full_execute requires rawdata_read_only_confirmed=true.",
            ))
            return ApprovalGateResult(
                ok=False,
                execution_allowed=False,
                approval_required=True,
                approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        risk_ack = appr_dict.get("risk_acknowledgement")
        if risk_ack is not True:
            errors.append(ApprovalGateIssue(
                "NATIVE_PREPROC_RISK_ACKNOWLEDGEMENT_REQUIRED",
                "native_preproc_full_execute requires risk_acknowledgement=true.",
            ))
            return ApprovalGateResult(
                ok=False,
                execution_allowed=False,
                approval_required=True,
                approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        subject_scope = appr_dict.get("subject_scope_confirmed")
        if subject_scope is not True:
            errors.append(ApprovalGateIssue(
                "NATIVE_PREPROC_SUBJECT_SCOPE_CONFIRMATION_REQUIRED",
                "native_preproc_full_execute requires subject_scope_confirmed=true.",
            ))
            return ApprovalGateResult(
                ok=False,
                execution_allowed=False,
                approval_required=True,
                approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

    # ── 12. manual_required nodes block execution (MVP) ──
    if manual_required_nodes:
        errors.append(ApprovalGateIssue(
            "MANUAL_REQUIRED_NODE",
            f"Manual-required nodes not yet supported: {', '.join(manual_required_nodes)}",
        ))
        return ApprovalGateResult(
            ok=False, execution_allowed=False,
            approval_required=True, approved=True,
            missing_approval_nodes=list(approval_required_nodes),
            errors=errors,
        )

    # ── 13. External-tool safety acknowledgements (M6-T004) ──
    external_tool_nodes = high_risk_backend_nodes or high_risk_nodes
    if external_tool_nodes:
        external_tool_ack = appr_dict.get("external_tool_acknowledgement")
        if external_tool_ack is not True:
            errors.append(ApprovalGateIssue(
                "EXTERNAL_TOOL_ACKNOWLEDGEMENT_REQUIRED",
                "High-risk external-tool nodes require explicit external_tool_acknowledgement=true. "
                "This confirms awareness that MATLAB/SPM/DPABI will be invoked.",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        rawdata_confirm = appr_dict.get("rawdata_read_only_confirmed")
        if rawdata_confirm is not True:
            errors.append(ApprovalGateIssue(
                "RAWDATA_READ_ONLY_CONFIRMATION_REQUIRED",
                "High-risk external-tool nodes require rawdata_read_only_confirmed=true.",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        output_confirm = appr_dict.get("output_directory_confirmed")
        if output_confirm is not True:
            errors.append(ApprovalGateIssue(
                "OUTPUT_DIRECTORY_CONFIRMATION_REQUIRED",
                "High-risk external-tool nodes require output_directory_confirmed=true.",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        risk_ack = appr_dict.get("risk_acknowledgement")
        if risk_ack is not True:
            errors.append(ApprovalGateIssue(
                "RISK_ACKNOWLEDGEMENT_REQUIRED",
                "High-risk external-tool nodes require risk_acknowledgement=true.",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        overwrite_policy = appr_dict.get("overwrite_policy")
        ALLOWED_OVERWRITE = {"fail_if_exists", "require_explicit_overwrite_approval"}
        if overwrite_policy not in ALLOWED_OVERWRITE:
            errors.append(ApprovalGateIssue(
                "OVERWRITE_POLICY_REQUIRED",
                f"High-risk external-tool nodes require overwrite_policy in {ALLOWED_OVERWRITE}. "
                f"Got: {overwrite_policy!r}",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

        subject_scope = appr_dict.get("subject_scope_confirmed")
        if subject_scope is not True:
            errors.append(ApprovalGateIssue(
                "SUBJECT_SCOPE_CONFIRMATION_REQUIRED",
                "High-risk external-tool nodes require subject_scope_confirmed=true.",
            ))
            return ApprovalGateResult(
                ok=False, execution_allowed=False,
                approval_required=True, approved=True,
                missing_approval_nodes=list(approval_required_nodes),
                errors=errors,
            )

    # ── 14. high risk approved → warning ──
    if high_risk_nodes:
        warnings.append(ApprovalGateIssue(
            "HIGH_RISK_APPROVED",
            f"High-risk nodes approved: {', '.join(high_risk_nodes)}. Proceed with caution.",
            severity="warning",
        ))
    if native_full_execute_nodes:
        warnings.append(ApprovalGateIssue(
            "NATIVE_PREPROC_APPROVED",
            "Native full preprocessing execution approved with rawdata-readonly and no-external-tool acknowledgements.",
            severity="warning",
        ))

    return ApprovalGateResult(
        ok=True, execution_allowed=True,
        approval_required=True, approved=True,
        missing_approval_nodes=[],
        warnings=warnings,
    )
