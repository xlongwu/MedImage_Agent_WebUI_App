"""Fail-closed capability and filesystem checks performed before every runner."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime.execution_gateway import current_allowlist_hash
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.runtime.tool_execution_context import ToolExecutionContext
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.node_contract import NodeContract
from src.backend.app.schemas.pipeline_schema import PipelineNode

_READ_FIELDS = {
    "atlas",
    "dataset_index",
    "input",
    "input_bold",
    "input_file",
    "input_path",
    "input_root",
    "mask",
    "rawdata_dir",
    "source",
    "source_dir",
    "spm_path",
}
_WRITE_FIELDS = {
    "destination",
    "destination_dir",
    "derivatives_dir",
    "log_dir",
    "output",
    "output_dir",
    "output_file",
    "output_path",
    "output_root",
    "report_dir",
    "target",
    "target_dir",
    "work_dir",
}


def _is_within(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _canonical(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _values(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list | tuple):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _classify_field(name: str) -> str | None:
    key = name.lower()
    if key in _WRITE_FIELDS or key.startswith("output_") or key.startswith("target_"):
        return "write"
    if key in _READ_FIELDS or key.startswith("input_") or key.startswith("source_"):
        return "read"
    if key.endswith(("_path", "_dir", "_root")):
        return "ambiguous"
    return None


def _node_paths(node: PipelineNode, contract: NodeContract) -> list[tuple[str, str, str]]:
    paths: list[tuple[str, str, str]] = []
    for value in node.inputs:
        if isinstance(value, str) and value.strip():
            paths.append(("read", "inputs", value))
    for value in node.outputs:
        if isinstance(value, str) and value.strip():
            paths.append(("write", "outputs", value))
    for key, value in node.params.items():
        rule = contract.parameter_schema.get(str(key))
        classification = (
            None if rule and rule.path_access == "non_path"
            else rule.path_access if rule and rule.path_access
            else _classify_field(str(key))
        )
        if classification is None:
            continue
        values = _values(value)
        if classification == "ambiguous" and values:
            raise SafetyError(
                f"CAPABILITY_PATH_FIELD_AMBIGUOUS: {node.id}.{key}",
                code="CAPABILITY_PATH_FIELD_AMBIGUOUS",
            )
        paths.extend((classification, str(key), item) for item in values)
    return paths


def _reject(
    context: ToolExecutionContext,
    *,
    reason: str,
    node: PipelineNode,
    details: dict[str, object] | None = None,
) -> None:
    context.ticket_service.record_rejection(
        project_id=context.project_id,
        ticket_id=context.execution_ticket_id,
        audit_id=context.audit_id,
        reason=reason,
        details={"node_id": node.id, "backend": node.backend, **(details or {})},
    )
    raise SafetyError(reason, code=reason, details=details or {})


def enforce_node_capabilities(
    context: ToolExecutionContext,
    node: PipelineNode,
) -> None:
    """Verify node/backend/path authority before any runner or input read occurs."""
    if context.ticket.is_expired():
        _reject(context, reason="EXECUTION_TICKET_EXPIRED", node=node)
    if context.allowlist_hash != current_allowlist_hash():
        _reject(context, reason="EXECUTION_TICKET_ALLOWLIST_MISMATCH", node=node)
    if node.id not in context.approved_node_ids:
        _reject(context, reason="CAPABILITY_NODE_NOT_APPROVED", node=node)
    try:
        contract = get_node_contract(node.id)
    except KeyError:
        _reject(context, reason="CAPABILITY_NODE_CONTRACT_MISSING", node=node)
        return
    expected_version = context.contract_versions.get(node.id)
    if not contract.executable:
        _reject(context, reason="CAPABILITY_NODE_CONTRACT_NOT_EXECUTABLE", node=node)
    if expected_version != contract.contract_version:
        _reject(context, reason="CAPABILITY_CONTRACT_VERSION_MISMATCH", node=node)
    if node.contract_version and node.contract_version != expected_version:
        _reject(context, reason="CAPABILITY_PIPELINE_CONTRACT_VERSION_MISMATCH", node=node)
    if node.backend not in context.approved_backend_ids:
        _reject(context, reason="CAPABILITY_BACKEND_NOT_APPROVED", node=node)

    try:
        paths = _node_paths(node, contract)
    except SafetyError as exc:
        _reject(
            context,
            reason=str(exc.code or "CAPABILITY_PATH_FIELD_AMBIGUOUS"),
            node=node,
        )
        return

    for access, field, raw_value in paths:
        resolved = _canonical(raw_value)
        if access == "read":
            allowed = _is_within(
                resolved,
                (*context.input_roots, *context.output_roots),
            )
            reason = "CAPABILITY_READ_PATH_OUTSIDE_ROOT"
        else:
            allowed = _is_within(resolved, context.output_roots)
            reason = "CAPABILITY_WRITE_PATH_OUTSIDE_ROOT"
            if allowed and _is_within(resolved, context.readonly_roots):
                allowed = False
                reason = "CAPABILITY_RAWDATA_WRITE_FORBIDDEN"
        if not allowed:
            _reject(
                context,
                reason=reason,
                node=node,
                details={
                    "field": field,
                    "access": access,
                    "requested_path": raw_value,
                    "resolved_path": str(resolved),
                },
            )


def enforce_recovery_pipeline_scope(
    ticket: ExecutionTicket,
    *,
    pipeline_node_ids: Iterable[str],
    run_id: str,
) -> None:
    """Fail before any runner when a child pipeline exceeds its exact scope."""
    if ticket.ticket_kind != "recovery_child":
        return
    if run_id != ticket.recovery_run_id:
        raise SafetyError("RECOVERY_RUN_ID_MISMATCH", code="RECOVERY_RUN_ID_MISMATCH")
    if set(pipeline_node_ids) != set(ticket.recovery_node_ids):
        raise SafetyError(
            "RECOVERY_PIPELINE_NODE_SCOPE_MISMATCH",
            code="RECOVERY_PIPELINE_NODE_SCOPE_MISMATCH",
        )
    if not ticket.output_roots or ticket.output_namespace is None:
        raise SafetyError("RECOVERY_OUTPUT_NAMESPACE_REQUIRED", code="RECOVERY_OUTPUT_NAMESPACE_REQUIRED")
    for output_root in ticket.output_roots:
        path = _canonical(output_root)
        if any(path == root for root in map(_canonical, ticket.readonly_roots)):
            raise SafetyError("RECOVERY_OUTPUT_READONLY_COLLISION", code="RECOVERY_OUTPUT_READONLY_COLLISION")


def filter_recovery_subjects(
    ticket: ExecutionTicket,
    subjects: list[dict[str, object]],
) -> list[dict[str, object]]:
    if ticket.ticket_kind != "recovery_child" or not ticket.recovery_subject_ids:
        return subjects
    requested = set(ticket.recovery_subject_ids)
    available = {str(item.get("subject_id")) for item in subjects}
    if not requested.issubset(available):
        raise SafetyError(
            "RECOVERY_SUBJECT_SCOPE_MISMATCH",
            code="RECOVERY_SUBJECT_SCOPE_MISMATCH",
            details={"missing_subjects": sorted(requested - available)},
        )
    return [item for item in subjects if str(item.get("subject_id")) in requested]
