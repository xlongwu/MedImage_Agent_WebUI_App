"""Tests for Approval Gate — approval record validation."""

from __future__ import annotations

import json

from src.backend.app.planner.approval_gate import (
    ApprovalRecord,
    check_approval_gate,
)

# ── Helpers ──


def _valid_validation(**overrides):
    v = {
        "ok": True,
        "approval_required_nodes": [],
        "high_risk_nodes": [],
        "manual_required_nodes": [],
        "risk_summary": {"requires_approval": False},
    }
    v.update(overrides)
    return v


def _approval(approved=True, approved_nodes=None, rejected_nodes=None):
    return ApprovalRecord(
        approved=approved,
        approved_by="test-user",
        approved_nodes=approved_nodes or [],
        rejected_nodes=rejected_nodes or [],
        external_tool_acknowledgement=True,
        rawdata_read_only_confirmed=True,
        output_directory_confirmed=True,
        risk_acknowledgement=True,
        overwrite_policy="fail_if_exists",
        subject_scope_confirmed=True,
    )


# ── 1. Validation missing ──


def test_validation_missing():
    result = check_approval_gate({}, None, None)  # type: ignore[arg-type]
    assert result.execution_allowed is False
    assert any(e.code == "VALIDATION_MISSING" for e in result.errors)


# ── 2. Validation not ok ──


def test_validation_not_ok():
    result = check_approval_gate({}, {"ok": False}, None)
    assert result.execution_allowed is False
    assert any(e.code == "VALIDATION_NOT_OK" for e in result.errors)


# ── 3. No approval needed → allowed ──


def test_no_approval_needed():
    result = check_approval_gate({}, _valid_validation(), None)
    assert result.execution_allowed is True
    assert result.approval_required is False


# ── 4. Approval needed but missing ──


def test_approval_missing():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    result = check_approval_gate({}, v, None)
    assert result.execution_allowed is False
    assert any(e.code == "APPROVAL_MISSING" for e in result.errors)


# ── 5. approved=false ──


def test_approved_false():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    a = _approval(approved=False)
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "APPROVAL_NOT_GRANTED" for e in result.errors)


# ── 6. approved_nodes cover required ──


def test_approved_nodes_cover_required():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    a = _approval(approved_nodes=["spm_realign_subject"])
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is True


# ── 7. approved_nodes missing required ──


def test_approved_nodes_missing_required():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    a = _approval(approved_nodes=["data_inspection"])
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "APPROVAL_NODE_MISSING" for e in result.errors)


# ── 8. Wildcard approval ──


def test_wildcard_approval():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject", "spm_smooth_subject"])
    a = _approval(approved_nodes=["*"])
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is True


# ── 9. rejected_nodes block ──


def test_rejected_nodes_block():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    a = _approval(approved_nodes=["spm_realign_subject"], rejected_nodes=["spm_smooth_subject"])
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "APPROVAL_REJECTED_NODE" for e in result.errors)


# ── 10. High risk → warning ──


def test_high_risk_warning():
    v = _valid_validation(
        approval_required_nodes=["spm_realign_subject"],
        high_risk_nodes=["spm_realign_subject"],
    )
    a = _approval(approved_nodes=["spm_realign_subject"])
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is True
    assert any(w.code == "HIGH_RISK_APPROVED" for w in result.warnings)


# ── 11. Manual required → blocked ──


def test_manual_required_blocked():
    v = _valid_validation(
        approval_required_nodes=["spm_realign_subject"],
        manual_required_nodes=["manual_intervention_required"],
    )
    a = _approval(approved_nodes=["*"])
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "MANUAL_REQUIRED_NODE" for e in result.errors)


# ── 12. to_dict JSON serializable ──


def test_to_dict_json():
    result = check_approval_gate({}, _valid_validation(), None)
    d = result.to_dict()
    raw = json.dumps(d, ensure_ascii=False)
    back = json.loads(raw)
    assert back["execution_allowed"] is True


# ── 13. No pipeline execution ──


def test_no_pipeline_execution():
    result = check_approval_gate({}, _valid_validation(), None)
    assert result.execution_allowed is True


# ── 14. No node runner ──


def test_no_runner():
    check_approval_gate({}, _valid_validation(), None)


# ── 15. No file writes ──


def test_no_file_writes(tmp_path):
    import os

    before = set(os.listdir(tmp_path))
    check_approval_gate({}, _valid_validation(), None)
    after = set(os.listdir(tmp_path))
    assert after == before


# ── 16. Dict approval accepted ──


def test_dict_approval():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    a = {"approved": True, "approved_nodes": ["spm_realign_subject"], "rejected_nodes": []}
    result = check_approval_gate({}, v, a)
    assert result.execution_allowed is True


# ── 17. Risk summary requires_approval triggers ──


def test_risk_summary_triggers_approval():
    v = _valid_validation(risk_summary={"requires_approval": True})
    result = check_approval_gate({}, v, None)
    assert result.execution_allowed is False
    assert result.approval_required is True


# ══════════════════════════════════════════════════════════════════════════════
# M6-T003: node-level + backend-level approval
# ══════════════════════════════════════════════════════════════════════════════


def _hr_plan(nodes=None):
    """Build a plan with high-risk backend nodes."""
    return {
        "pipeline_id": "test",
        "nodes": nodes
        or [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}},
        ],
    }


def _hr_approval(
    approved_nodes=None,
    approved_backends=None,
    rejected_nodes=None,
    ext_ack=True,
    rawdata_ok=True,
    output_ok=True,
    risk_ok=True,
    overwrite="fail_if_exists",
    subj_ok=True,
):
    return ApprovalRecord(
        approved=True,
        approved_by="test-user",
        approved_nodes=approved_nodes or [],
        rejected_nodes=rejected_nodes or [],
        approved_backends=approved_backends or [],
        external_tool_acknowledgement=ext_ack,
        rawdata_read_only_confirmed=rawdata_ok,
        output_directory_confirmed=output_ok,
        risk_acknowledgement=risk_ok,
        overwrite_policy=overwrite,
        subject_scope_confirmed=subj_ok,
    )


# ── 18. ApprovalRecord supports approved_backends ──


def test_approval_record_supports_approved_backends():
    a = ApprovalRecord(
        approved=True,
        approved_backends=["matlab-spm"],
    )
    assert a.approved_backends == ["matlab-spm"]


# ── 19. SPM node with wildcard → blocked ──


def test_spm_node_wildcard_blocked():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    plan = _hr_plan()
    a = _hr_approval(approved_nodes=["*"])
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is False
    assert any(
        e.code == "WILDCARD_APPROVAL_NOT_ALLOWED_FOR_HIGH_RISK_BACKEND" for e in result.errors
    )


# ── 20. SPM node without explicit node approval → blocked ──


def test_spm_node_no_explicit_approval():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    plan = _hr_plan()
    a = _hr_approval(approved_nodes=["data_inspection"], approved_backends=["matlab-spm"])
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "HIGH_RISK_NODE_REQUIRES_EXPLICIT_APPROVAL" for e in result.errors)


# ── 21. SPM node without backend approval → blocked ──


def test_spm_node_no_backend_approval():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    plan = _hr_plan()
    a = _hr_approval(approved_nodes=["spm_realign_subject"], approved_backends=[])
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "HIGH_RISK_BACKEND_REQUIRES_APPROVAL" for e in result.errors)


# ── 22. SPM node with explicit node + backend approval → passes ──


def test_spm_node_full_approval_passes():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    plan = _hr_plan()
    a = _hr_approval(approved_nodes=["spm_realign_subject"], approved_backends=["matlab-spm"])
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is True


# ── 23. Python-only node still works with wildcard ──


def test_python_only_node_wildcard_still_works():
    v = _valid_validation(approval_required_nodes=["data_inspection"])
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}},
        ],
    }
    a = _hr_approval(approved_nodes=["*"])
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is True


# ── 24. DPABI execution node requires explicit node + backend ──


def test_dpabi_execution_requires_explicit():
    v = _valid_validation(approval_required_nodes=["dpabi_subject_smooth"])
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "dpabi_subject_smooth", "backend": "dpabi", "depends_on": [], "params": {}},
        ],
    }
    a = _hr_approval(approved_nodes=["*"])
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is False  # wildcard blocked


# ── 25. DPABI contract Python-only node NOT treated as high-risk ──


def test_dpabi_contract_python_not_high_risk():
    v = _valid_validation()
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "dpabi_capability_inspection",
                "backend": "python",
                "depends_on": [],
                "params": {},
            },
        ],
    }
    result = check_approval_gate(plan, v, None)  # no approval needed
    assert result.execution_allowed is True


# ── 26. Rejected nodes still block (priority) ──


def test_rejected_blocks_even_with_backend_approval():
    v = _valid_validation(approval_required_nodes=["spm_realign_subject"])
    plan = _hr_plan()
    a = _hr_approval(
        approved_nodes=["spm_realign_subject"],
        approved_backends=["matlab-spm"],
        rejected_nodes=["spm_realign_subject"],
    )
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "APPROVAL_REJECTED_NODE" for e in result.errors)


# ── 27. Manual required still blocks ──


def test_manual_required_still_blocks():
    v = _valid_validation(manual_required_nodes=["manual_intervention_required"])
    plan = _hr_plan()
    a = _hr_approval(
        approved_nodes=["spm_realign_subject", "manual_intervention_required"],
        approved_backends=["matlab-spm"],
    )
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is False
    assert any(e.code == "MANUAL_REQUIRED_NODE" for e in result.errors)


# ── 28. High risk approved still warns ──


def test_high_risk_still_warns():
    v = _valid_validation(
        approval_required_nodes=["spm_realign_subject"],
        high_risk_nodes=["spm_realign_subject"],
    )
    plan = _hr_plan()
    a = _hr_approval(approved_nodes=["spm_realign_subject"], approved_backends=["matlab-spm"])
    result = check_approval_gate(plan, v, a)
    assert result.execution_allowed is True
    assert any(w.code == "HIGH_RISK_APPROVED" for w in result.warnings)
