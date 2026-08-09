from __future__ import annotations

from src.backend.app.runtime.node_contract_consistency import (
    assert_node_contract_consistency,
    node_contract_consistency_issues,
)
from src.backend.app.runtime.node_contract_registry import NODE_CONTRACTS
from src.backend.app.runtime.node_registry import NODE_REGISTRY


def test_current_registry_contract_and_catalog_are_consistent() -> None:
    assert node_contract_consistency_issues() == ()
    assert_node_contract_consistency()


def test_missing_contract_and_unsafe_write_root_fail_closed() -> None:
    contracts = dict(NODE_CONTRACTS)
    contracts.pop("data_inspection")
    unsafe = contracts["contract_smoke"].model_copy(update={"write_roots": ("rawdata",)})
    contracts["contract_smoke"] = unsafe

    issues = node_contract_consistency_issues(
        registry=NODE_REGISTRY,
        contracts=contracts,
        catalog=[],
    )

    assert any("registered nodes missing contracts" in issue for issue in issues)
    assert any("unsafe write roots" in issue for issue in issues)
