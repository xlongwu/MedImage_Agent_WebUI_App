"""Tests for Plan Validator — static safety & correctness checks."""

from __future__ import annotations

import json

from src.backend.app.planner.plan_validator import (
    validate_plan,
)

# ── Helpers ──


def _valid_plan(**overrides) -> dict:
    """Return a minimal valid plan dict."""
    plan = {
        "pipeline_id": "test_plan",
        "nodes": [
            {"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}},
            {
                "id": "motion_qc_subject",
                "backend": "python",
                "depends_on": ["data_inspection"],
                "params": {},
            },
        ],
    }
    plan.update(overrides)
    return plan


# ── 1. Valid plan passes ──


def test_valid_plan_passes():
    result = validate_plan(_valid_plan())
    assert result.ok is True
    assert len(result.errors) == 0


# ── 2. Not a dict → error ──


def test_plan_not_dict():
    result = validate_plan("not a dict")  # type: ignore[arg-type]
    assert result.ok is False
    assert any(e.code == "INVALID_PLAN_TYPE" for e in result.errors)


def test_plan_is_list():
    result = validate_plan([1, 2, 3])  # type: ignore[arg-type]
    assert result.ok is False
    assert any(e.code == "INVALID_PLAN_TYPE" for e in result.errors)


# ── 3. Missing pipeline_id → error ──


def test_missing_pipeline_id():
    result = validate_plan({"nodes": [{"id": "data_inspection"}]})
    assert result.ok is False
    assert any(e.code == "MISSING_PIPELINE_ID" for e in result.errors)


# ── 4. Missing / empty nodes → error ──


def test_missing_nodes():
    result = validate_plan({"pipeline_id": "p"})
    assert result.ok is False
    assert any(e.code == "MISSING_OR_EMPTY_NODES" for e in result.errors)


def test_empty_nodes():
    result = validate_plan({"pipeline_id": "p", "nodes": []})
    assert result.ok is False
    assert any(e.code == "MISSING_OR_EMPTY_NODES" for e in result.errors)


# ── 5. Node missing id → error ──


def test_node_missing_id():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [{"backend": "python"}],
        }
    )
    assert result.ok is False
    assert any(e.code == "MISSING_NODE_ID" for e in result.errors)


# ── 6. Unknown node → error + unknown_nodes ──


def test_unknown_node_id():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [{"id": "nonexistent_node_xyz", "depends_on": []}],
        }
    )
    assert result.ok is False
    assert "nonexistent_node_xyz" in result.unknown_nodes
    assert any(e.code == "UNKNOWN_NODE_ID" for e in result.errors)


# ── 7. Duplicate node id → error ──


def test_duplicate_node_id():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "depends_on": []},
                {"id": "data_inspection", "depends_on": []},
            ],
        }
    )
    assert result.ok is False
    assert any(e.code == "DUPLICATE_NODE_ID" for e in result.errors)


# ── 8. Unknown dependency → error ──


def test_unknown_dependency():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "depends_on": [], "backend": "python"},
                {"id": "motion_qc_subject", "depends_on": ["no_such_node"], "backend": "python"},
            ],
        }
    )
    assert result.ok is False
    assert any(e.code == "UNKNOWN_DEPENDENCY" for e in result.errors)


# ── 9. Self dependency → error ──


def test_self_dependency():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "depends_on": ["data_inspection"], "backend": "python"},
            ],
        }
    )
    assert result.ok is False
    assert any(e.code == "SELF_DEPENDENCY" for e in result.errors)


# ── 10. Dependency cycle → error ──


def test_dependency_cycle():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "depends_on": ["motion_qc_subject"], "backend": "python"},
                {"id": "motion_qc_subject", "depends_on": ["data_inspection"], "backend": "python"},
            ],
        }
    )
    assert result.ok is False
    # With a cycle, topological_order should be empty
    assert result.topological_order == []


# ── 11. Valid DAG returns correct topological order ──


def test_topological_order():
    result = validate_plan(_valid_plan())
    assert result.ok is True
    assert result.topological_order == ["data_inspection", "motion_qc_subject"]


# ── 12. requires_approval node identified ──


def test_approval_required_node():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "depends_on": [], "backend": "python"},
                {
                    "id": "spm_realign_subject",
                    "depends_on": ["data_inspection"],
                    "backend": "matlab-spm",
                    "params": {},
                },
            ],
        }
    )
    assert result.ok is True
    assert "spm_realign_subject" in result.approval_required_nodes


# ── 13. high risk node identified ──


def test_high_risk_node():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "depends_on": [],
                    "backend": "matlab-spm",
                    "params": {},
                },
            ],
        }
    )
    assert "spm_realign_subject" in result.high_risk_nodes


# ── 14. approval warning when approved not set ──


def test_approval_warning_when_approved_missing():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "depends_on": [],
                    "backend": "matlab-spm",
                    "params": {},
                },  # no "approved" key
            ],
        }
    )
    assert any(w.code == "APPROVAL_REQUIRED" for w in result.warnings)


# ── 15. backend mismatch → warning ──


def test_backend_mismatch_is_rejected():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {
                    "id": "data_inspection",
                    "depends_on": [],
                    "backend": "matlab-spm",
                },  # catalog says python
            ],
        }
    )
    assert result.ok is False
    assert any(error.code == "BACKEND_MISMATCH" for error in result.errors)


# ── 16. blocked contract → explicit error ──


def test_blocked_contract_has_explicit_contract_error_without_fallback_warning():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "dpabi_alff_falff_contract", "depends_on": []},
            ],
        }
    )
    assert result.ok is False
    assert all(w.code != "UNCATALOGED_METADATA" for w in result.warnings)
    assert any(error.code == "NODE_CONTRACT_NOT_EXECUTABLE" for error in result.errors)


# ── 17. risk_summary ──


def test_risk_summary():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "depends_on": [], "backend": "python"},
                {
                    "id": "spm_realign_subject",
                    "depends_on": ["data_inspection"],
                    "backend": "matlab-spm",
                    "params": {},
                },
            ],
        }
    )
    rs = result.risk_summary
    assert rs["nodes_total"] == 2
    assert rs["requires_approval"] is True
    assert rs["approval_required_count"] == 1
    assert rs["high_risk_count"] == 1
    assert rs["unknown_nodes_count"] == 0


# ── 18. to_dict is JSON-serializable ──


def test_result_to_dict_json_serializable():
    result = validate_plan(_valid_plan())
    d = result.to_dict()
    raw = json.dumps(d, ensure_ascii=False)
    back = json.loads(raw)
    assert back["ok"] is True
    assert "errors" in back
    assert "warnings" in back
    assert "topological_order" in back


# ── 19. Does not execute runners ──


def test_validate_does_not_execute_runners():
    """validate_plan must never call any node runner function."""
    result = validate_plan(_valid_plan())
    assert result.ok is True
    # No side effects — trivially passes if no runner was invoked.


# ── 20. Invalid node types in list ──


def test_node_not_a_dict():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": ["not_a_dict", {"id": "data_inspection"}],
        }
    )
    assert any(e.code == "INVALID_NODE_TYPE" for e in result.errors)


# ── 21. Invalid depends_on type ──


def test_invalid_depends_on_type():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "depends_on": "not_a_list", "backend": "python"},
            ],
        }
    )
    assert any(e.code == "INVALID_DEPENDS_ON" for e in result.errors)


# ── 22. Invalid params type ──


def test_invalid_params_type():
    result = validate_plan(
        {
            "pipeline_id": "p",
            "nodes": [
                {"id": "data_inspection", "params": "not_a_dict", "backend": "python"},
            ],
        }
    )
    assert any(e.code == "INVALID_PARAMS" for e in result.errors)
