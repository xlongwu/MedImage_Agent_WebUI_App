"""Fail-closed consistency checks for registry, contracts, and catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.backend.app.runtime.node_contract_registry import NODE_CONTRACTS
from src.backend.app.runtime.node_registry import NODE_REGISTRY
from src.backend.app.runtime.tool_catalog import ToolCatalogItem, build_tool_catalog
from src.backend.app.schemas.node_contract import NodeContract


APPROVED_WRITE_ROOTS = frozenset({"data", "work", "logs", "reports", "derivatives", "exports"})


def node_contract_consistency_issues(
    *,
    registry: Mapping[str, object] | None = None,
    contracts: Mapping[str, NodeContract] | None = None,
    catalog: Sequence[ToolCatalogItem] | None = None,
) -> tuple[str, ...]:
    registry_map = registry if registry is not None else NODE_REGISTRY
    contract_map = contracts if contracts is not None else NODE_CONTRACTS
    catalog_items = list(catalog) if catalog is not None else build_tool_catalog()
    issues: list[str] = []

    missing_contracts = sorted(set(registry_map) - set(contract_map))
    if missing_contracts:
        issues.append(f"registered nodes missing contracts: {missing_contracts}")

    catalog_ids = [item.id for item in catalog_items]
    duplicates = sorted({node_id for node_id in catalog_ids if catalog_ids.count(node_id) > 1})
    if duplicates:
        issues.append(f"duplicate catalog node ids: {duplicates}")
    if set(catalog_ids) != set(contract_map):
        issues.append(
            "catalog/contract ids differ: "
            f"missing={sorted(set(contract_map) - set(catalog_ids))}, "
            f"extra={sorted(set(catalog_ids) - set(contract_map))}"
        )

    catalog_by_id = {item.id: item for item in catalog_items}
    for node_id, contract in sorted(contract_map.items()):
        if contract.node_id != node_id:
            issues.append(f"contract key mismatch for {node_id}: {contract.node_id}")
        if contract.resources.backend != contract.backend:
            issues.append(f"resource backend mismatch for {node_id}")
        if contract.executable and node_id not in registry_map:
            issues.append(f"executable contract has no registered runner: {node_id}")
        if contract.executable and not contract.output_schema:
            issues.append(f"executable contract has no output artifacts: {node_id}")
        unsafe_roots = sorted(set(contract.write_roots) - APPROVED_WRITE_ROOTS)
        if unsafe_roots:
            issues.append(f"unsafe write roots for {node_id}: {unsafe_roots}")
        item = catalog_by_id.get(node_id)
        if item is None:
            continue
        expected = (
            contract.backend,
            contract.parallel_level,
            contract.requires_approval,
            contract.manual_required,
            contract.risk_level,
            [value.artifact_type for value in contract.input_schema],
            [value.artifact_type for value in contract.output_schema],
        )
        actual = (
            item.backend,
            item.parallel_level,
            item.requires_approval,
            item.manual_required,
            item.risk_level,
            item.inputs,
            item.outputs,
        )
        if actual != expected:
            issues.append(f"catalog safety fields differ from contract for {node_id}")
    return tuple(issues)


def assert_node_contract_consistency() -> None:
    issues = node_contract_consistency_issues()
    if issues:
        raise RuntimeError("NODE_CONTRACT_CONSISTENCY_FAILED: " + "; ".join(issues))
