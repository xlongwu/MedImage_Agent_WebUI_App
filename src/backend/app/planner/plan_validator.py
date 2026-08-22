"""Plan Validator — static safety & correctness checks for pipeline plans.

The Plan Validator sits between the LLM Planner and the Pipeline Executor.
It validates that a candidate plan (dict) references only known nodes,
has legal dependencies, is acyclic, and surfaces risk information from the
authoritative NodeContract registry so that the Human Approval Gate can make
informed decisions.

This module is read-only: it never executes any node runner, never calls
MATLAB/SPM/DPABI, and never writes files.
"""

from __future__ import annotations

import re
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import (
    NODE_CONTRACTS,
    get_node_contract,
    validate_and_normalize_parameters,
)
from src.backend.app.runtime.node_registry import NODE_REGISTRY
from src.backend.app.schemas.goal_contract import GoalContract
from src.backend.app.services.spm_realign_params import validate_spm_realign_params

# ── Output dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlanValidationIssue:
    """A single issue found during plan validation."""

    code: str
    message: str
    node_id: str | None = None
    severity: str = "error"  # "error" | "warning"


@dataclass(frozen=True)
class PlanValidationResult:
    """Full result of validating a pipeline plan."""

    ok: bool
    errors: list[PlanValidationIssue] = field(default_factory=list)
    warnings: list[PlanValidationIssue] = field(default_factory=list)
    nodes_total: int = 0
    approval_required_nodes: list[str] = field(default_factory=list)
    manual_required_nodes: list[str] = field(default_factory=list)
    high_risk_nodes: list[str] = field(default_factory=list)
    unknown_nodes: list[str] = field(default_factory=list)
    topological_order: list[str] = field(default_factory=list)
    risk_summary: dict[str, Any] = field(default_factory=dict)
    normalized_plan: dict[str, Any] = field(default_factory=dict)
    normalized_params_hash: str = ""
    contract_versions: dict[str, str] = field(default_factory=dict)
    validation_evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "ok": self.ok,
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
            "nodes_total": self.nodes_total,
            "approval_required_nodes": self.approval_required_nodes,
            "manual_required_nodes": self.manual_required_nodes,
            "high_risk_nodes": self.high_risk_nodes,
            "unknown_nodes": self.unknown_nodes,
            "topological_order": self.topological_order,
            "risk_summary": self.risk_summary,
            "normalized_plan": self.normalized_plan,
            "normalized_params_hash": self.normalized_params_hash,
            "contract_versions": self.contract_versions,
            "validation_evidence": self.validation_evidence,
        }


# ── Public entry point ────────────────────────────────────────────────────────

def validate_plan(plan: dict[str, Any]) -> PlanValidationResult:
    """Validate a pipeline plan dict and return a structured result.

    The plan dict should have:
      - pipeline_id: str
      - nodes: list[dict] (each with at least "id")

    Returns a PlanValidationResult.  ok=True means no errors (warnings
    are advisory and do not block ok).
    """
    errors: list[PlanValidationIssue] = []
    warnings: list[PlanValidationIssue] = []

    # ── 1. Structural checks ──
    if not isinstance(plan, dict):
        return PlanValidationResult(
            ok=False,
            errors=[PlanValidationIssue(
                code="INVALID_PLAN_TYPE",
                message="Plan must be a dictionary.",
            )],
        )

    pipeline_id = plan.get("pipeline_id")
    if not pipeline_id or not isinstance(pipeline_id, str):
        errors.append(PlanValidationIssue(
            code="MISSING_PIPELINE_ID",
            message="Plan must have a non-empty 'pipeline_id' string.",
        ))

    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or len(nodes) == 0:
        errors.append(PlanValidationIssue(
            code="MISSING_OR_EMPTY_NODES",
            message="Plan must have a non-empty 'nodes' list.",
        ))
        return _build_result(plan, errors, warnings)

    # ── Per-node structural checks ──
    node_ids: list[str] = []
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(PlanValidationIssue(
                code="INVALID_NODE_TYPE",
                message=f"Node at index {i} must be a dictionary.",
            ))
            continue
        nid = node.get("id")
        if not nid or not isinstance(nid, str):
            errors.append(PlanValidationIssue(
                code="MISSING_NODE_ID",
                message=f"Node at index {i} is missing a valid 'id'.",
            ))
            continue
        node_ids.append(nid)
        deps = node.get("depends_on")
        if deps is not None and not isinstance(deps, list):
            errors.append(PlanValidationIssue(
                code="INVALID_DEPENDS_ON",
                message=f"Node '{nid}' has non-list 'depends_on'.",
                node_id=nid,
            ))
        params = node.get("params")
        if params is not None and not isinstance(params, dict):
            errors.append(PlanValidationIssue(
                code="INVALID_PARAMS",
                message=f"Node '{nid}' has non-dict 'params'.",
                node_id=nid,
            ))

    if not node_ids:
        return _build_result(plan, errors, warnings)

    # ── 2. Duplicate node ids ──
    seen: set[str] = set()
    for nid in node_ids:
        if nid in seen:
            errors.append(PlanValidationIssue(
                code="DUPLICATE_NODE_ID",
                message=f"Duplicate node id: '{nid}'.",
                node_id=nid,
            ))
        seen.add(nid)

    # ── 3. Node Contract validation ──
    contract_ids = set(NODE_CONTRACTS)

    unknown_nodes: list[str] = []
    for nid in node_ids:
        if nid not in contract_ids:
            unknown_nodes.append(nid)
            errors.append(PlanValidationIssue(
                code=("NODE_CONTRACT_MISSING" if nid in NODE_REGISTRY else "UNKNOWN_NODE_ID"),
                message=(
                    f"Registered node id '{nid}' has no Node Contract."
                    if nid in NODE_REGISTRY
                    else f"Node id '{nid}' is not in the Node Contract registry."
                ),
                node_id=nid,
            ))

    # ── 4. Dependency checks ──
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if not nid or nid in unknown_nodes:
            continue
        deps = node.get("depends_on", []) or []
        for dep in deps:
            if dep not in node_ids:
                errors.append(PlanValidationIssue(
                    code="UNKNOWN_DEPENDENCY",
                    message=f"Node '{nid}' depends on '{dep}' which is not in the plan.",
                    node_id=nid,
                ))
            if dep == nid:
                errors.append(PlanValidationIssue(
                    code="SELF_DEPENDENCY",
                    message=f"Node '{nid}' depends on itself.",
                    node_id=nid,
                ))

    # ── 5. Cycle detection (Kahn's algorithm) ──
    topo_order: list[str] = []
    has_dep_error = any(e.code in ("UNKNOWN_DEPENDENCY", "SELF_DEPENDENCY") for e in errors)
    if not has_dep_error:
        topo_order = _topological_sort(node_ids, nodes)
        if len(topo_order) < len(node_ids):
            errors.append(PlanValidationIssue(
                code="DEPENDENCY_CYCLE",
                message="The plan contains a dependency cycle.",
            ))

    # ── 6. Approval / risk from Node Contract ──
    approval_required: list[str] = []
    manual_required: list[str] = []
    high_risk: list[str] = []
    uncataloged_count = 0
    normalized_plan = deepcopy(plan)
    normalized_nodes = normalized_plan.get("nodes", [])
    contract_versions: dict[str, str] = {}
    validation_evidence: list[dict[str, Any]] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if not nid or nid in unknown_nodes:
            continue
        try:
            contract = get_node_contract(nid)
        except KeyError:
            errors.append(PlanValidationIssue(
                code="NODE_CONTRACT_MISSING",
                message=f"Node '{nid}' has no registered execution contract.",
                node_id=nid,
            ))
            continue
        contract_versions[nid] = contract.contract_version
        if not contract.executable and contract.capability_level == "unavailable":
            errors.append(PlanValidationIssue(
                code="NODE_CONTRACT_NOT_EXECUTABLE",
                message=f"Node '{nid}' is not an executable contract candidate.",
                node_id=nid,
            ))
        elif not contract.executable:
            warnings.append(PlanValidationIssue(
                code="NODE_CONTRACT_SCAFFOLDED",
                message=(
                    f"Node '{nid}' is reviewable under contract "
                    f"'{contract.contract_version}' but is excluded from runtime authority."
                ),
                node_id=nid,
                severity="warning",
            ))

        if contract.requires_approval:
            approval_required.append(nid)
            node_params = node.get("params", {}) or {}
            if "approved" not in node_params:
                warnings.append(PlanValidationIssue(
                    code="APPROVAL_REQUIRED",
                    message=f"Node '{nid}' requires approval but 'approved' is not set in params.",
                    node_id=nid,
                    severity="warning",
                ))

        if contract.manual_required:
            manual_required.append(nid)

        if contract.risk_level == "high":
            high_risk.append(nid)

        # Backend mismatches are contract violations, not advisory warnings.
        node_backend = node.get("backend")
        if (
            node_backend
            and contract.validation_policy.enforce_backend
            and node_backend != contract.backend
            and contract.backend != "unknown"
        ):
            errors.append(PlanValidationIssue(
                code="BACKEND_MISMATCH",
                message=f"Node '{nid}' declares backend '{node_backend}' but contract requires '{contract.backend}'.",
                node_id=nid,
            ))

        params = node.get("params", {}) or {}
        if isinstance(params, dict):
            normalized_params, evidence, parameter_errors = (
                validate_and_normalize_parameters(contract, params)
            )
            for message in parameter_errors:
                errors.append(PlanValidationIssue(
                    code="NODE_PARAMETER_INVALID",
                    message=message,
                    node_id=nid,
                ))
            if evidence is not None:
                validation_evidence.append(evidence.model_dump(mode="json"))
                for normalized_node in normalized_nodes:
                    if isinstance(normalized_node, dict) and normalized_node.get("id") == nid:
                        normalized_node["params"] = normalized_params
                        normalized_node["contract_version"] = contract.contract_version
                        break

        for field_name, schema in (
            ("input_types", contract.input_schema),
            ("output_types", contract.output_schema),
        ):
            declared = node.get(field_name)
            if declared is None:
                continue
            if not isinstance(declared, list) or not all(isinstance(v, str) for v in declared):
                errors.append(PlanValidationIssue(
                    code="NODE_ARTIFACT_TYPE_INVALID",
                    message=f"Node '{nid}' field '{field_name}' must be a list of strings.",
                    node_id=nid,
                ))
                continue
            allowed = {value.artifact_type for value in schema}
            invalid = sorted(set(declared) - allowed)
            if invalid:
                errors.append(PlanValidationIssue(
                    code="NODE_ARTIFACT_TYPE_INVALID",
                    message=f"Node '{nid}' declares unsupported {field_name}: {invalid}.",
                    node_id=nid,
                ))

        # SPM realign specific: warn about non-executable status
        if nid == "spm_realign_subject":
            warnings.append(PlanValidationIssue(
                code="SPM_REALIGN_NODE_NOT_EXECUTABLE",
                message=(
                    "Node 'spm_realign_subject' is metadata-only and not currently "
                    "executable.  Real SPM/MATLAB execution requires approval gate, "
                    "persisted audit, environment checks, and safe-allowlist opt-in."
                ),
                node_id=nid,
                severity="warning",
            ))

    # ── 7. Per-node parameter validation ──
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if not nid or nid in unknown_nodes:
            continue
        params = node.get("params") or {}

        if nid == "spm_realign_subject" and params:
            _, param_warnings, param_errors = validate_spm_realign_params(params)
            for err_msg in param_errors:
                errors.append(PlanValidationIssue(
                    code="SPM_REALIGN_PARAM_INVALID",
                    message=err_msg,
                    node_id=nid,
                    severity="error",
                ))
            for warn_msg in param_warnings:
                warnings.append(PlanValidationIssue(
                    code="SPM_REALIGN_PARAM_WARNING",
                    message=warn_msg,
                    node_id=nid,
                    severity="warning",
                ))

    # ── 8. Build result ──
    risk_summary = {
        "nodes_total": len(node_ids),
        "requires_approval": len(approval_required) > 0,
        "approval_required_count": len(approval_required),
        "manual_required": len(manual_required) > 0,
        "manual_required_count": len(manual_required),
        "high_risk_count": len(high_risk),
        "unknown_nodes_count": len(unknown_nodes),
        "has_uncataloged_metadata": uncataloged_count > 0,
    }

    normalized_bindings = {
        node_id: {
            "contract_version": contract_versions[node_id],
            "params": next(
                (
                    item.get("params", {})
                    for item in normalized_nodes
                    if isinstance(item, dict) and item.get("id") == node_id
                ),
                {},
            ),
        }
        for node_id in sorted(contract_versions)
    }
    normalized_params_hash = stable_hash(normalized_bindings) if normalized_bindings else ""
    metadata = normalized_plan.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["contract_versions"] = dict(sorted(contract_versions.items()))
        metadata["normalized_params_hash"] = normalized_params_hash

    return PlanValidationResult(
        ok=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        nodes_total=len(node_ids),
        approval_required_nodes=approval_required,
        manual_required_nodes=manual_required,
        high_risk_nodes=high_risk,
        unknown_nodes=unknown_nodes,
        topological_order=topo_order if len(topo_order) == len(node_ids) else [],
        risk_summary=risk_summary,
        normalized_plan=normalized_plan,
        normalized_params_hash=normalized_params_hash,
        contract_versions=dict(sorted(contract_versions.items())),
        validation_evidence=validation_evidence,
    )


def validate_goal_contract_reachability(
    plan: dict[str, Any],
    goal_contract: GoalContract,
) -> list[PlanValidationIssue]:
    """Verify that reviewed criteria can be produced by the normalized plan."""
    issues: list[PlanValidationIssue] = []
    nodes = [node for node in plan.get("nodes", []) if isinstance(node, dict)]
    node_ids = {str(node.get("id")) for node in nodes if node.get("id")}
    output_types: set[str] = set()
    capability_levels: list[str] = []
    for node_id in sorted(node_ids):
        try:
            contract = get_node_contract(node_id)
        except (KeyError, SafetyError):
            issues.append(
                PlanValidationIssue(
                    code="GOAL_NODE_CONTRACT_MISSING",
                    message=f"Goal Contract references node '{node_id}' without a contract.",
                    node_id=node_id,
                )
            )
            continue
        output_types.update(
            re.sub(r"[^a-z0-9]+", "_", artifact.artifact_type.lower()).strip("_")
            for artifact in contract.output_schema
        )
        capability_levels.append(contract.capability_level)
    aliases = {
        "functional_connectivity_matrix": "fc_matrix",
    }
    output_types = {aliases.get(value, value) for value in output_types}
    for criterion in goal_contract.criteria:
        if criterion.criterion_type in {
            "artifact_present",
            "artifact_reloadable",
            "artifact_registered",
        }:
            target = aliases.get(criterion.target, criterion.target)
            if target not in output_types:
                issues.append(
                    PlanValidationIssue(
                        code="GOAL_ARTIFACT_UNREACHABLE",
                        message=f"Goal artifact '{criterion.target}' is not declared by the plan's node contracts.",
                    )
                )
        if criterion.criterion_type == "node_status":
            required_nodes = {
                str(item) for item in criterion.expected.get("node_ids", [])
            }
            missing = sorted(required_nodes - node_ids)
            if missing:
                issues.append(
                    PlanValidationIssue(
                        code="GOAL_NODE_UNREACHABLE",
                        message=f"Goal Contract requires nodes not present in the plan: {missing}.",
                    )
                )
    order = ["unavailable", "scaffolded", "metadata_only", "computed", "validated"]
    if capability_levels:
        # Auxiliary metadata nodes do not lower a scientific output node's
        # defensible capability. Artifact criteria above already prove that a
        # declared producer exists, so use the strongest reachable contract
        # rather than the weakest node in a mixed pipeline.
        reachable = max(capability_levels, key=order.index)
        if order.index(reachable) < order.index(goal_contract.minimum_capability_level):
            issues.append(
                PlanValidationIssue(
                    code="GOAL_CAPABILITY_UNREACHABLE",
                    message=(
                        f"Goal requires '{goal_contract.minimum_capability_level}' but the "
                        f"plan can defend at most '{reachable}'."
                    ),
                )
            )
    return issues


# ── Helpers ───────────────────────────────────────────────────────────────────

def _topological_sort(node_ids: list[str], nodes: list[dict[str, Any]]) -> list[str]:
    """Kahn's algorithm. Returns topological order or partial on cycle."""
    in_degree: dict[str, int] = dict.fromkeys(node_ids, 0)
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if not nid:
            continue
        for dep in node.get("depends_on", []) or []:
            if dep in adj:
                adj[dep].append(nid)
                in_degree[nid] += 1

    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    while queue:
        nid = queue.popleft()
        order.append(nid)
        for neighbor in adj.get(nid, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return order


def _build_result(
    plan: dict[str, Any],
    errors: list[PlanValidationIssue],
    warnings: list[PlanValidationIssue],
) -> PlanValidationResult:
    """Build result when structural checks block further validation."""
    return PlanValidationResult(
        ok=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
