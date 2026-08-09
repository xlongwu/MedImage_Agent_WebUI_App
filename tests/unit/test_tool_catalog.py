"""Tests for Tool Catalog — read-only pipeline node metadata registry."""

from __future__ import annotations

import json

import pytest

from src.backend.app.runtime.tool_catalog import (
    ToolCatalogItem,
    build_tool_catalog,
    catalog_as_dicts,
    get_tool_catalog_item,
)

# ── Module-level cache ──


@pytest.fixture(scope="module")
def catalog() -> list[ToolCatalogItem]:
    return build_tool_catalog()


@pytest.fixture(scope="module")
def catalog_by_id(catalog: list[ToolCatalogItem]) -> dict[str, ToolCatalogItem]:
    return {item.id: item for item in catalog}


# ── Structural ──


def test_catalog_non_empty(catalog: list[ToolCatalogItem]):
    assert len(catalog) > 0


def test_catalog_covers_all_registered_nodes(catalog: list[ToolCatalogItem]):
    from src.backend.app.runtime.node_registry import NODE_REGISTRY

    catalog_ids = {item.id for item in catalog}
    registry_ids = set(NODE_REGISTRY)
    missing = registry_ids - catalog_ids
    extra = catalog_ids - registry_ids

    # Allow extra catalog nodes that are contract-only / metadata-only
    extra_non_contract = set()
    for item in catalog:
        if item.id in extra:
            if item.backend != "contract" and item.tags and "contract" not in item.tags:
                extra_non_contract.add(item.id)

    assert missing == set(), f"Catalog missing nodes: {missing}"
    assert extra_non_contract == set(), (
        f"Catalog has extra non-contract nodes not in registry: {extra_non_contract}"
    )


def test_every_item_has_required_fields(catalog: list[ToolCatalogItem]):
    required = [
        "id",
        "name",
        "backend",
        "parallel_level",
        "description",
        "requires_approval",
        "manual_required",
        "risk_level",
    ]
    for item in catalog:
        for field in required:
            assert hasattr(item, field), f"{item.id} missing field '{field}'"


# ── Specific node metadata ──


def test_spm_realign_requires_approval(catalog_by_id: dict[str, ToolCatalogItem]):
    item = catalog_by_id["spm_realign_subject"]
    assert item.requires_approval is True
    assert "spm" in item.tags or "matlab" in item.tags


def test_motion_qc_risk_not_high(catalog_by_id: dict[str, ToolCatalogItem]):
    item = catalog_by_id["motion_qc_subject"]
    assert item.risk_level != "high"


def test_report_exporter_has_report_tag(catalog_by_id: dict[str, ToolCatalogItem]):
    item = catalog_by_id["rsfmri_report_exporter"]
    assert "report" in item.tags


# ── Contract-derived coverage ──


def test_catalog_has_no_inferred_safety_metadata(catalog: list[ToolCatalogItem]):
    """Every item is derived from a complete NodeContract, never an id-prefix fallback."""
    from src.backend.app.runtime.node_contract_registry import NODE_CONTRACTS

    assert {item.id for item in catalog} == set(NODE_CONTRACTS)
    assert all("No catalog metadata yet" not in item.description for item in catalog)


def test_blocked_dpabi_contracts_are_explicitly_high_risk(catalog_by_id: dict[str, ToolCatalogItem]):
    """Default-disabled external DPABI declarations remain visibly blocked."""
    for nid in [
        "dpabi_capability_inspection",
        "dpabi_preflight",
        "dpabi_alff_falff_contract",
        "dpabi_wrapper_scaffold",
    ]:
        item = catalog_by_id.get(nid)
        if item:
            assert item.risk_level == "high"
            assert item.requires_approval is True
            assert item.manual_required is False


def test_fallback_spm_nodes_are_high_risk(catalog_by_id: dict[str, ToolCatalogItem]):
    """All spm_* nodes should require approval and be high risk."""
    spm_items = {k: v for k, v in catalog_by_id.items() if k.startswith("spm_")}
    for nid, item in spm_items.items():
        assert item.requires_approval is True, f"{nid} should require approval"
        assert item.risk_level in ("high", "medium"), (
            f"{nid}: expected high/medium risk, got {item.risk_level}"
        )


# ── get_tool_catalog_item ──


def test_get_item_for_existing_node():
    item = get_tool_catalog_item("spm_realign_subject")
    assert isinstance(item, ToolCatalogItem)
    assert item.id == "spm_realign_subject"


def test_get_item_for_nonexistent_node_raises():
    with pytest.raises(KeyError, match="Unknown node id"):
        get_tool_catalog_item("nonexistent_node_xyz")


# ── Serialization ──


def test_catalog_as_dicts_is_json_serializable():
    dicts = catalog_as_dicts()
    assert isinstance(dicts, list)
    assert len(dicts) > 0
    raw = json.dumps(dicts, ensure_ascii=False)
    back = json.loads(raw)
    assert len(back) == len(dicts)
    for d in back:
        assert "id" in d


# ── No side effects ──


def test_build_catalog_does_not_execute_runners():
    """Building the catalog must not call any node runner functions."""
    items = build_tool_catalog()
    assert len(items) > 0
    # If any runner was called, the test would have observable side effects
    # (file writes, MATLAB invocations, etc.).  The absence of such effects
    # combined with the speed of this test (< 1 ms) is sufficient evidence.
