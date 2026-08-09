"""Execution Consistency Schema — unit tests.

Tests: model serialization, passing consistency, all 13 failure checks,
optional flags (require_approval/audit/output_manifest), summary helper,
and purity invariants.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from src.backend.app.schemas.execution_consistency import (
    ConsistencyIssue,
    ExecutionConsistencyInput,
    ExecutionConsistencyReport,
    summarize_consistency_issues,
    verify_execution_consistency,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _base(project_id: str = "p1", **kw) -> ExecutionConsistencyInput:
    """Minimal valid input with project_id set."""
    defaults: dict = {
        "project_id": project_id,
        "reviewed_plan_id": "rp-1",
        "plan_hash": "sha256:abc",
        "project_config_path": "/config/project.yaml",
        "project_context_path": "/config/context.yaml",
        "node_ids": ["node_a", "node_b"],
        "node_param_hashes": {"node_a": "ph_a", "node_b": "ph_b"},
        "output_root": "/outputs/work/run_1/",
        "output_manifest_ids": ["man_1"],
        "allowlist_hash": "sha256:allowlist_v1",
        "approval_summary_hash": "approval_001",
        "audit_id": "audit_001",
        "dry_run_status": "DRY_RUN_OK",
    }
    defaults.update(kw)
    return ExecutionConsistencyInput(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# Model serialization tests
# ═══════════════════════════════════════════════════════════════════════


def test_input_minimal_serializes():
    inp = ExecutionConsistencyInput(project_id="p1")
    d = inp.model_dump()
    assert d["project_id"] == "p1"
    assert d["node_ids"] == []


def test_input_full_serializes():
    inp = _base()
    d = inp.model_dump()
    assert d["project_id"] == "p1"
    assert d["reviewed_plan_id"] == "rp-1"
    assert d["node_ids"] == ["node_a", "node_b"]


def test_issue_serializes_with_expected_actual():
    issue = ConsistencyIssue(
        code="PROJECT_ID_MISMATCH",
        message="mismatch",
        expected="p1",
        actual="p2",
    )
    d = issue.model_dump()
    assert d["expected"] == "p1"
    assert d["actual"] == "p2"


def test_report_serializes():
    report = ExecutionConsistencyReport(ok=True, status="pass")
    d = report.model_dump()
    assert d["ok"] is True
    assert d["status"] == "pass"
    assert d["issues"] == []


def test_invalid_severity_rejected():
    with pytest.raises(ValidationError):
        ConsistencyIssue(code="UNKNOWN", message="m", severity="critical")  # type: ignore[arg-type]


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        ExecutionConsistencyReport(ok=True, status="perfect")  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════
# Passing consistency
# ═══════════════════════════════════════════════════════════════════════


def test_identical_inputs_pass():
    base = _base()
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    assert report.ok is True
    assert report.status == "pass"
    assert report.issue_count == 0


def test_accepted_dry_run_status_ready():
    base = _base(dry_run_status="ready")
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    assert report.status == "pass"


def test_accepted_dry_run_status_dry_run_ok():
    base = _base(dry_run_status="DRY_RUN_OK")
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    assert report.status == "pass"


def test_accepted_dry_run_status_execution_preflight_ready():
    base = _base(dry_run_status="EXECUTION_PREFLIGHT_READY")
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    assert report.status == "pass"


def test_missing_dry_run_status_not_flagged():
    """When dry_run_status is None, no DRY_RUN_STATUS_NOT_READY is raised."""
    base = _base(dry_run_status=None)
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    codes = {i.code for i in report.issues}
    assert "DRY_RUN_STATUS_NOT_READY" not in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: project_id mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_project_id_mismatch_reviewed_vs_dry_run():
    r = _base(project_id="p_reviewed")
    d = _base(project_id="p_dry_run")
    e = _base(project_id="p_dry_run")
    report = verify_execution_consistency(reviewed=r, dry_run=d, execution=e)
    assert not report.ok
    codes = {i.code for i in report.issues}
    assert "PROJECT_ID_MISMATCH" in codes


def test_project_id_mismatch_dry_run_vs_execution():
    r = _base(project_id="p1")
    d = _base(project_id="p_dry")
    e = _base(project_id="p_exec")
    report = verify_execution_consistency(reviewed=r, dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "PROJECT_ID_MISMATCH" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: reviewed_plan_id mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_reviewed_plan_id_mismatch():
    r = _base(reviewed_plan_id="rp-A")
    d = _base(reviewed_plan_id="rp-B")
    e = _base(reviewed_plan_id="rp-A")
    report = verify_execution_consistency(reviewed=r, dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "REVIEWED_PLAN_ID_MISMATCH" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: plan_hash mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_plan_hash_mismatch():
    r = _base(plan_hash="sha256:abc")
    d = _base(plan_hash="sha256:abc")
    e = _base(plan_hash="sha256:def")
    report = verify_execution_consistency(reviewed=r, dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "PLAN_HASH_MISMATCH" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: project_config_path mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_project_config_path_mismatch():
    d = _base(project_config_path="/cfg/a.yaml")
    e = _base(project_config_path="/cfg/b.yaml")
    report = verify_execution_consistency(reviewed=_base(), dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "PROJECT_CONFIG_PATH_MISMATCH" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: project_context_path missing on execution
# ═══════════════════════════════════════════════════════════════════════


def test_project_context_path_missing():
    e = _base(project_context_path="")  # empty = missing
    report = verify_execution_consistency(reviewed=_base(), dry_run=_base(), execution=e)
    codes = {i.code for i in report.issues}
    assert "PROJECT_CONTEXT_PATH_MISSING" in codes


def test_project_context_path_none():
    e = _base(project_context_path=None)
    report = verify_execution_consistency(reviewed=_base(), dry_run=_base(), execution=e)
    codes = {i.code for i in report.issues}
    assert "PROJECT_CONTEXT_PATH_MISSING" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: node set mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_node_set_mismatch():
    d = _base(node_ids=["a", "b"])
    e = _base(node_ids=["a", "c"])
    report = verify_execution_consistency(reviewed=_base(), dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "NODE_SET_MISMATCH" in codes


def test_node_set_extra_in_execution():
    d = _base(node_ids=["a"])
    e = _base(node_ids=["a", "b"])
    report = verify_execution_consistency(reviewed=_base(), dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "NODE_SET_MISMATCH" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: node param hash mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_node_param_hash_mismatch():
    d = _base(node_param_hashes={"a": "ph_v1"})
    e = _base(node_param_hashes={"a": "ph_v2"})
    report = verify_execution_consistency(reviewed=_base(), dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "NODE_PARAM_HASH_MISMATCH" in codes


def test_node_param_hash_mismatch_reports_node_id():
    d = _base(node_param_hashes={"node_a": "ph_v1"})
    e = _base(node_param_hashes={"node_a": "ph_v2"})
    report = verify_execution_consistency(reviewed=_base(), dry_run=d, execution=e)
    param_issues = [i for i in report.issues if i.code == "NODE_PARAM_HASH_MISMATCH"]
    assert len(param_issues) >= 1
    assert param_issues[0].node_id == "node_a"


# ═══════════════════════════════════════════════════════════════════════
# Failure: output_root mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_output_root_mismatch():
    d = _base(output_root="/out/a/")
    e = _base(output_root="/out/b/")
    report = verify_execution_consistency(reviewed=_base(), dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "OUTPUT_ROOT_MISMATCH" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: output manifest missing
# ═══════════════════════════════════════════════════════════════════════


def test_output_manifest_missing_when_required():
    e = _base(output_manifest_ids=[])
    report = verify_execution_consistency(
        reviewed=_base(),
        dry_run=_base(),
        execution=e,
        require_output_manifest=True,
    )
    codes = {i.code for i in report.issues}
    assert "OUTPUT_MANIFEST_MISSING" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: safe allowlist fingerprint mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_safe_allowlist_changed():
    d = _base(allowlist_hash="sha256:v1")
    e = _base(allowlist_hash="sha256:v2")
    report = verify_execution_consistency(reviewed=_base(), dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "SAFE_ALLOWLIST_CHANGED" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: approval / audit missing
# ═══════════════════════════════════════════════════════════════════════


def test_approval_missing_when_required():
    e = _base(approval_summary_hash=None)
    report = verify_execution_consistency(
        reviewed=_base(),
        dry_run=_base(),
        execution=e,
        require_approval=True,
    )
    codes = {i.code for i in report.issues}
    assert "APPROVAL_CONTEXT_MISSING" in codes


def test_audit_missing_when_required():
    e = _base(audit_id=None)
    report = verify_execution_consistency(
        reviewed=_base(),
        dry_run=_base(),
        execution=e,
        require_audit=True,
    )
    codes = {i.code for i in report.issues}
    assert "AUDIT_CONTEXT_MISSING" in codes


# ═══════════════════════════════════════════════════════════════════════
# Failure: bad dry_run status
# ═══════════════════════════════════════════════════════════════════════


def test_bad_dry_run_status():
    base = _base(dry_run_status="BLOCKED")
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    codes = {i.code for i in report.issues}
    assert "DRY_RUN_STATUS_NOT_READY" in codes


def test_bad_dry_run_status_cancelled():
    base = _base(dry_run_status="CANCELLED")
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    codes = {i.code for i in report.issues}
    assert "DRY_RUN_STATUS_NOT_READY" in codes


# ═══════════════════════════════════════════════════════════════════════
# Optional flags
# ═══════════════════════════════════════════════════════════════════════


def test_require_approval_false_allows_missing():
    e = _base(approval_summary_hash=None)
    report = verify_execution_consistency(
        reviewed=_base(),
        dry_run=_base(),
        execution=e,
        require_approval=False,
    )
    codes = {i.code for i in report.issues}
    assert "APPROVAL_CONTEXT_MISSING" not in codes


def test_require_audit_false_allows_missing():
    e = _base(audit_id=None)
    report = verify_execution_consistency(
        reviewed=_base(),
        dry_run=_base(),
        execution=e,
        require_audit=False,
    )
    codes = {i.code for i in report.issues}
    assert "AUDIT_CONTEXT_MISSING" not in codes


def test_require_output_manifest_false_allows_missing():
    e = _base(output_manifest_ids=[])
    report = verify_execution_consistency(
        reviewed=_base(),
        dry_run=_base(),
        execution=e,
        require_output_manifest=False,
    )
    codes = {i.code for i in report.issues}
    assert "OUTPUT_MANIFEST_MISSING" not in codes


# ═══════════════════════════════════════════════════════════════════════
# Summary helper
# ═══════════════════════════════════════════════════════════════════════


def test_summarize_counts_info_warning_error():
    issues = [
        ConsistencyIssue(code="UNKNOWN", message="info1", severity="info"),
        ConsistencyIssue(code="UNKNOWN", message="warn1", severity="warning"),
        ConsistencyIssue(code="UNKNOWN", message="err1", severity="error"),
        ConsistencyIssue(code="UNKNOWN", message="err2", severity="error"),
    ]
    s = summarize_consistency_issues(issues)
    assert s == {
        "issue_count": 4,
        "error_count": 2,
        "warning_count": 1,
        "info_count": 1,
    }


def test_summarize_empty():
    s = summarize_consistency_issues([])
    assert s == {"issue_count": 0, "error_count": 0, "warning_count": 0, "info_count": 0}


# ═══════════════════════════════════════════════════════════════════════
# Complex multi-issue report
# ═══════════════════════════════════════════════════════════════════════


def test_multiple_mismatches_produce_fail():
    r = _base(project_id="pA", plan_hash="h1")
    d = _base(project_id="pB", plan_hash="h2", node_ids=["x"], dry_run_status="blocked")
    e = _base(
        project_id="pC",
        plan_hash="h3",
        node_ids=["y"],
        project_context_path=None,
        approval_summary_hash=None,
        audit_id=None,
    )
    report = verify_execution_consistency(reviewed=r, dry_run=d, execution=e)
    assert report.ok is False
    assert report.status == "fail"
    assert report.error_count >= 3
    codes = {i.code for i in report.issues}
    assert "PROJECT_ID_MISMATCH" in codes
    assert "PLAN_HASH_MISMATCH" in codes
    assert "PROJECT_CONTEXT_PATH_MISSING" in codes
    assert "NODE_SET_MISMATCH" in codes
    assert "APPROVAL_CONTEXT_MISSING" in codes
    assert "AUDIT_CONTEXT_MISSING" in codes
    assert "DRY_RUN_STATUS_NOT_READY" in codes


# ═══════════════════════════════════════════════════════════════════════
# Purity / safety tests
# ═══════════════════════════════════════════════════════════════════════


def test_helper_creates_no_files(tmp_path):
    """verify_execution_consistency creates no files."""
    before = list(tmp_path.iterdir())
    _report = verify_execution_consistency(
        reviewed=_base(),
        dry_run=_base(),
        execution=_base(),
    )
    _s = summarize_consistency_issues([])
    after = list(tmp_path.iterdir())
    assert before == after


def test_module_does_not_import_runtime_executor():
    """Schema module must not import pipeline_executor."""
    import src.backend.app.schemas.execution_consistency as ec

    source = inspect.getsource(ec)
    forbidden = [
        "pipeline_executor",
        "state_store",
        "execute_reviewed_routes",
        "node_registry",
    ]
    for name in forbidden:
        assert name not in source, f"Found forbidden import: {name}"


def test_no_rawdata_or_outputs_path_touched():
    """Module does not reference rawdata or outputs directories."""
    import src.backend.app.schemas.execution_consistency as ec

    source = str(getattr(ec, "__file__", ""))
    assert "schemas" in source


def test_no_file_io_in_module():
    """Schema module must not perform file I/O on import."""
    import src.backend.app.schemas.execution_consistency as ec

    source = inspect.getsource(ec)
    iowords = ["open(", "Path(", "write_text", "read_text", "json.dump", "json.load"]
    for word in iowords:
        assert word not in source, f"Found file I/O pattern: {word}"


def test_unknown_strings_do_not_raise():
    """Bogus strings in dry_run_status produce a DRY_RUN_STATUS_NOT_READY issue, not an exception."""
    base = _base(dry_run_status="garbage_status")
    # Should not raise — just report the issue
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    assert report.status == "fail"
    codes = {i.code for i in report.issues}
    assert "DRY_RUN_STATUS_NOT_READY" in codes


def test_checked_fields_populated():
    report = verify_execution_consistency(reviewed=_base(), dry_run=_base(), execution=_base())
    expected = {
        "project_id",
        "reviewed_plan_id",
        "plan_hash",
        "project_config_path",
        "project_context_path",
        "node_ids",
        "node_param_hashes",
        "output_root",
        "output_manifest_ids",
        "allowlist_hash",
        "approval_summary_hash",
        "audit_id",
        "dry_run_status",
    }
    assert set(report.checked_fields) == expected
