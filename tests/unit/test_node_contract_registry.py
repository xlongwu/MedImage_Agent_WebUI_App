from __future__ import annotations

import json

from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.runtime.node_contract_registry import (
    NODE_CONTRACTS,
    executable_contract_versions,
    get_node_contract,
    validate_and_normalize_parameters,
)
from src.backend.app.runtime.node_registry import NODE_REGISTRY
from src.backend.app.runtime.tool_catalog import build_tool_catalog


def _plan(node_id: str, *, backend: str, params: dict | None = None, **node_fields):
    return {
        "pipeline_id": "contract-test",
        "nodes": [
            {
                "id": node_id,
                "backend": backend,
                "depends_on": [],
                "params": params or {},
                **node_fields,
            }
        ],
    }


def test_every_registered_node_has_a_serializable_versioned_contract():
    assert set(NODE_REGISTRY) <= set(NODE_CONTRACTS)
    assert executable_contract_versions()
    for node_id, contract in NODE_CONTRACTS.items():
        assert contract.node_id == node_id
        assert contract.contract_version
        payload = contract.model_dump(mode="json")
        json.dumps(payload)
        assert "input_schema" in payload
        assert "parameter_schema" in payload
        assert "output_schema" in payload
        assert "preconditions" in payload
        assert "postconditions" in payload
        assert "side_effects" in payload
        assert "resources" in payload
        assert "retry_policy" in payload
        assert "idempotency" in payload
        assert "validation_policy" in payload
        assert "requires_approval" in payload
        assert "manual_required" in payload
        assert "risk_level" in payload
        assert "write_roots" in payload


def test_strict_contract_rejects_type_range_unknown_fields_and_normalizes_defaults():
    contract = get_node_contract("temporal_filtering_subject")
    normalized, evidence, errors = validate_and_normalize_parameters(
        contract,
        {"low_hz": "bad", "high_hz": 2.0, "invented": True},
    )
    assert evidence is None
    assert "unknown parameters" in " ".join(errors)
    assert "must be number" in " ".join(errors)
    assert "must be <=" in " ".join(errors)

    first = validate_plan(_plan("temporal_filtering_subject", backend="python", params={}))
    second = validate_plan(_plan("temporal_filtering_subject", backend="python", params={}))
    assert first.ok and second.ok
    params = first.normalized_plan["nodes"][0]["params"]
    assert params["low_hz"] == 0.01
    assert params["high_hz"] == 0.08
    assert params["backend"] == "python"
    assert first.normalized_params_hash == second.normalized_params_hash
    assert first.contract_versions == {"temporal_filtering_subject": "1.0.0"}


def test_invalid_artifact_type_precondition_and_backend_are_rejected_before_review():
    artifact = validate_plan(
        _plan(
            "data_inspection",
            backend="python",
            output_types=["invented-artifact"],
        )
    )
    assert any(error.code == "NODE_ARTIFACT_TYPE_INVALID" for error in artifact.errors)

    precondition = validate_plan(
        _plan("native_preproc_full_execute", backend="native_python", params={})
    )
    assert any(
        error.code == "NODE_PARAMETER_INVALID" and "missing required" in error.message
        for error in precondition.errors
    )

    backend = validate_plan(_plan("data_inspection", backend="gpu"))
    assert any(error.code == "BACKEND_MISMATCH" for error in backend.errors)


def test_native_dry_run_contract_accepts_registered_bids_input():
    result = validate_plan(
        _plan(
            "native_preproc_full_dry_run",
            backend="native_python",
            params={"input_bids_dir": "C:/research/demo/rawdata"},
        )
    )

    assert result.ok is True
    assert result.normalized_plan["nodes"][0]["params"]["input_bids_dir"] == (
        "C:/research/demo/rawdata"
    )


def test_native_execution_hash_binds_reviewed_subject_scope():
    common = {
        "input_bids_dir": "C:/research/demo/rawdata",
        "confirmations": {},
    }
    sub_001 = validate_plan(
        _plan(
            "native_preproc_full_execute",
            backend="native_python",
            params={**common, "subject_id": "sub-001"},
        )
    )
    sub_002 = validate_plan(
        _plan(
            "native_preproc_full_execute",
            backend="native_python",
            params={**common, "subject_id": "sub-002"},
        )
    )

    assert sub_001.ok is True
    assert sub_002.ok is True
    assert sub_001.normalized_plan["nodes"][0]["params"]["subject_id"] == "sub-001"
    assert sub_001.normalized_params_hash != sub_002.normalized_params_hash


def test_contracts_are_authoritative_and_no_legacy_or_permissive_contract_remains():
    contract = get_node_contract("dpabi_alff_falff_contract")
    assert contract.capability_level in {"unavailable", "metadata_only"}
    assert contract.executable is False

    for item in NODE_CONTRACTS.values():
        assert "legacy" not in item.contract_version
        assert item.validation_policy.compatibility_mode is None
        assert item.validation_policy.deprecation is None
        if item.executable:
            assert item.validation_policy.allow_additional_parameters is False

    result = validate_plan(
        _plan("dpabi_alff_falff_contract", backend="unknown", params={"legacy": 1})
    )
    assert result.ok is False
    assert any(error.code == "NODE_CONTRACT_NOT_EXECUTABLE" for error in result.errors)


def test_tool_catalog_safe_fields_are_derived_from_node_contracts():
    catalog = {item.id: item for item in build_tool_catalog()}
    assert set(catalog) == set(NODE_CONTRACTS)
    for node_id, contract in NODE_CONTRACTS.items():
        item = catalog[node_id]
        assert item.backend == contract.backend
        assert item.parallel_level == contract.parallel_level
        assert item.requires_approval == contract.requires_approval
        assert item.manual_required == contract.manual_required
        assert item.risk_level == contract.risk_level
        assert item.inputs == [value.artifact_type for value in contract.input_schema]
        assert item.outputs == [value.artifact_type for value in contract.output_schema]


def test_missing_contract_fails_closed(monkeypatch):
    monkeypatch.delitem(NODE_CONTRACTS, "data_inspection")
    result = validate_plan(_plan("data_inspection", backend="python"))
    assert result.ok is False
    assert any(error.code == "NODE_CONTRACT_MISSING" for error in result.errors)


def test_recovery_capabilities_default_false_and_first_batch_is_explicit():
    blocked = get_node_contract("dpabi_alff_falff_contract")
    assert blocked.retry_policy.retryable is False
    assert blocked.retry_policy.supports_subject_subset is False
    assert blocked.retry_policy.supports_resume is False
    assert blocked.retry_policy.checkpoint_schema is None
    assert blocked.retry_policy.mutable_parameters_for_recovery == ()
    assert blocked.retry_policy.backend_switch_targets == ()
    assert blocked.idempotency.attempt_output_strategy == "none"

    fc = get_node_contract("functional_connectivity_subject")
    assert fc.retry_policy.retryable is True
    assert fc.retry_policy.supports_subject_subset is True
    assert fc.retry_policy.supports_resume is False
    assert "roi_count" in fc.retry_policy.mutable_parameters_for_recovery
    assert fc.idempotency.output_collision_policy == "isolated_attempt"
    assert fc.idempotency.attempt_output_strategy == "isolated_subdirectory"
    assert all(
        getattr(fc.retry_policy, field) is not None
        for field in (
            "max_lifecycle_recovery_attempts",
            "max_node_attempts",
            "max_subject_node_attempts",
            "max_replans",
            "max_recovery_wall_seconds",
        )
    )
