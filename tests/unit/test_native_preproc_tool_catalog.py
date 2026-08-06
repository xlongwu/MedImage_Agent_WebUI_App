from __future__ import annotations

from src.backend.app.native_preproc.orchestrator.stage_graph import iter_native_full_stage_specs
from src.backend.app.runtime.tool_catalog import build_tool_catalog


def _catalog_by_id():
    return {item.id: item for item in build_tool_catalog()}


def test_native_full_execute_has_explicit_catalog_metadata() -> None:
    catalog = _catalog_by_id()

    execute = catalog["native_preproc_full_execute"]
    dry_run = catalog["native_preproc_full_dry_run"]

    assert execute.backend == "native_python"
    assert execute.requires_approval is True
    assert execute.risk_level == "medium"
    assert "uncataloged" not in execute.tags
    assert "no-external-tools" in execute.tags
    assert "rawdata-readonly" in execute.tags
    assert "MATLAB" in execute.description

    assert dry_run.backend == "native_python"
    assert dry_run.requires_approval is False
    assert dry_run.risk_level == "low"
    assert "uncataloged" not in dry_run.tags


def test_native_stage_boundary_nodes_do_not_use_fallback_metadata() -> None:
    catalog = _catalog_by_id()

    for spec in iter_native_full_stage_specs():
        item = catalog[spec.node_id]
        assert item.backend == "native_python"
        if spec.node_id == "native_auto_acpc_align":
            assert item.requires_approval is True
            assert item.risk_level == "medium"
            assert "acpc" in item.tags
            assert "stage-boundary" not in item.tags
            continue
        assert item.risk_level == "low"
        assert "uncataloged" not in item.tags
        assert "stage-boundary" in item.tags
        assert "native_preproc_full_execute" in item.description
