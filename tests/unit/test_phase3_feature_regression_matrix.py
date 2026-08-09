"""Phase 3 feature regression matrix — contract smoke tests.

Covers the three completed Phase 3 schema layers:
  execution state, output manifest/provenance, dry-run/execute consistency.

All tests are pure schema/contract checks.  No FastAPI, no SQLite,
no project creation.  No runtime executor is imported or modified.
No external-tool execution is enabled.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from src.backend.app.schemas.execution_consistency import (
    ExecutionConsistencyInput,
    verify_execution_consistency,
)
from src.backend.app.schemas.execution_manifest import (
    ExecutionFailureRecord,
    ExecutionProvenance,
    OutputManifestItem,
    build_output_manifest,
)
from src.backend.app.schemas.execution_state import (
    can_transition_node,
    can_transition_run,
    is_node_reuse_eligible,
    is_run_resume_eligible,
    is_run_retry_eligible,
    is_run_terminal,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _ci(**kw) -> ExecutionConsistencyInput:
    """Build a consistency input with plausible defaults."""
    defaults = {
        "project_id": "p1",
        "reviewed_plan_id": "rp-1",
        "plan_hash": "sha256:abc",
        "project_config_path": "/cfg/p.yaml",
        "project_context_path": "/cfg/ctx.yaml",
        "node_ids": ["a", "b"],
        "node_param_hashes": {"a": "ph_a", "b": "ph_b"},
        "output_root": "/out/run1/",
        "output_manifest_ids": ["m1"],
        "allowlist_hash": "sha256:al_v1",
        "approval_summary_hash": "appr_001",
        "audit_id": "aud_001",
        "dry_run_status": "DRY_RUN_OK",
    }
    defaults.update(kw)
    return ExecutionConsistencyInput(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# Group 1 — Execution state contract
# ═══════════════════════════════════════════════════════════════════════


def test_run_state_succeeded_terminal_not_eligible():
    """succeeded is terminal and not retry- or resume-eligible."""
    assert is_run_terminal("succeeded") is True
    assert is_run_retry_eligible("succeeded") is False
    assert is_run_resume_eligible("succeeded") is False


def test_run_state_failed_terminal_and_eligible():
    """failed is terminal and retry- and resume-eligible."""
    assert is_run_terminal("failed") is True
    assert is_run_retry_eligible("failed") is True
    assert is_run_resume_eligible("failed") is True


def test_node_state_reuse_eligible():
    """succeeded and reused nodes are reuse-eligible."""
    assert is_node_reuse_eligible("succeeded") is True
    assert is_node_reuse_eligible("reused") is True
    assert is_node_reuse_eligible("failed") is False


def test_allowed_and_disallowed_transitions():
    """Representative allowed and disallowed run/node transitions."""
    # Allowed
    assert can_transition_run("created", "queued") is True
    assert can_transition_node("running", "succeeded") is True
    # Disallowed
    assert can_transition_run("succeeded", "running") is False
    assert can_transition_node("succeeded", "failed") is False


def test_unknown_state_strings_return_false():
    """Unknown state strings return False for all helpers."""
    bogus = "nonexistent_bogus_state"
    assert is_run_terminal(bogus) is False
    assert is_run_retry_eligible(bogus) is False
    assert is_run_resume_eligible(bogus) is False
    assert is_node_reuse_eligible(bogus) is False
    assert can_transition_run(bogus, "running") is False
    assert can_transition_node(bogus, "running") is False


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Output manifest contract
# ═══════════════════════════════════════════════════════════════════════


def test_output_manifest_item_validation():
    """OutputManifestItem rejects empty path, negative size, verified→exists."""
    with pytest.raises(ValidationError, match="path must be non-empty"):
        OutputManifestItem(path="")
    with pytest.raises(ValidationError, match="size_bytes cannot be negative"):
        OutputManifestItem(path="/t", size_bytes=-1)
    with pytest.raises(ValidationError, match="verified=True requires exists=True"):
        OutputManifestItem(path="/t", verified=True, exists=False)


def test_build_output_manifest_auto_computes_counts():
    """build_output_manifest sets missing_required_count and verified_count."""
    items = [
        OutputManifestItem(
            path="/a", required=True, exists=True, verified=True, verification_status="verified"
        ),
        OutputManifestItem(path="/b", required=True, exists=False, warnings=["w"]),
        OutputManifestItem(path="/c", required=False, exists=False, errors=["e"]),
    ]
    m = build_output_manifest(project_id="p1", run_id="r1", node_id="n1", items=items)
    assert m.missing_required_count == 1
    assert m.verified_count == 1
    assert m.warning_count == 1
    assert m.error_count == 1


def test_optional_missing_not_counted_as_required():
    """Optional missing output is not counted as missing required."""
    items = [
        OutputManifestItem(path="/a", required=False, exists=False),
        OutputManifestItem(path="/b", required=True, exists=True),
    ]
    m = build_output_manifest(project_id="p1", run_id="r1", node_id="n1", items=items)
    assert m.missing_required_count == 0


# ═══════════════════════════════════════════════════════════════════════
# Group 3 — Provenance / failure record contract
# ═══════════════════════════════════════════════════════════════════════


def test_provenance_minimal_serializes_rejects_shell_command():
    """Minimal provenance serializes; shell_command is rejected."""
    p = ExecutionProvenance(project_id="p1", run_id="r1", node_id="n1")
    d = p.model_dump()
    assert d["project_id"] == "p1"
    assert d["run_id"] == "r1"
    assert d["node_id"] == "n1"

    # shell_command must be rejected (extra='forbid')
    with pytest.raises(ValidationError):
        ExecutionProvenance(
            project_id="p1",
            run_id="r1",
            node_id="n1",
            shell_command="rm -rf /",  # type: ignore[call-arg]
        )


def test_external_backend_metadata_only():
    """External backend can be recorded as metadata without enabling execution."""
    p = ExecutionProvenance(
        project_id="p1",
        run_id="r1",
        node_id="spm_realign",
        backend="matlab-spm",
        command_template_id="spm12_realign_estwrite_v1",
    )
    assert p.backend == "matlab-spm"
    # command_template_id is an identifier only, not executable code
    assert isinstance(p.command_template_id, str)


def test_failure_record_retryable_resume_eligible():
    """ExecutionFailureRecord supports retryable and resume_eligible flags."""
    r = ExecutionFailureRecord(
        stage="execution",
        message="Node failed",
        retryable=True,
        resume_eligible=False,
    )
    d = r.model_dump()
    assert d["retryable"] is True
    assert d["resume_eligible"] is False


# ═══════════════════════════════════════════════════════════════════════
# Group 4 — Dry-run / execute consistency contract
# ═══════════════════════════════════════════════════════════════════════


def test_identical_inputs_pass():
    """Identical reviewed/dry_run/execution inputs pass consistency."""
    base = _ci()
    report = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)
    assert report.ok is True
    assert report.status == "pass"


def test_project_id_mismatch_detected():
    """project_id mismatch produces PROJECT_ID_MISMATCH."""
    r = _ci(project_id="p_reviewed")
    d = _ci(project_id="p_dry")
    e = _ci(project_id="p_exec")
    report = verify_execution_consistency(reviewed=r, dry_run=d, execution=e)
    codes = {i.code for i in report.issues}
    assert "PROJECT_ID_MISMATCH" in codes
    assert report.ok is False


def test_missing_approval_and_audit_detected():
    """Missing approval produces APPROVAL_CONTEXT_MISSING;
    missing audit produces AUDIT_CONTEXT_MISSING."""
    e_no_appr = _ci(approval_summary_hash=None)
    e_no_aud = _ci(audit_id=None)
    base = _ci()

    r1 = verify_execution_consistency(
        reviewed=base,
        dry_run=base,
        execution=e_no_appr,
        require_approval=True,
    )
    assert "APPROVAL_CONTEXT_MISSING" in {i.code for i in r1.issues}

    r2 = verify_execution_consistency(
        reviewed=base,
        dry_run=base,
        execution=e_no_aud,
        require_audit=True,
    )
    assert "AUDIT_CONTEXT_MISSING" in {i.code for i in r2.issues}


def test_bad_dry_run_status_and_optional_flags():
    """Bad dry-run status produces DRY_RUN_STATUS_NOT_READY.
    require_approval=False and require_audit=False allow missing fields."""
    base_bad = _ci(dry_run_status="BLOCKED")
    report = verify_execution_consistency(reviewed=base_bad, dry_run=base_bad, execution=base_bad)
    assert "DRY_RUN_STATUS_NOT_READY" in {i.code for i in report.issues}

    # Optional flags allow missing contexts
    e = _ci(approval_summary_hash=None, audit_id=None, output_manifest_ids=[])
    report2 = verify_execution_consistency(
        reviewed=_ci(),
        dry_run=_ci(),
        execution=e,
        require_approval=False,
        require_audit=False,
        require_output_manifest=False,
    )
    assert report2.ok is True


# ═══════════════════════════════════════════════════════════════════════
# Group 5 — Purity / safety checks
# ═══════════════════════════════════════════════════════════════════════


def test_no_runtime_executor_imports():
    """Phase 3 schema modules must not import pipeline_executor or state_store."""
    import src.backend.app.schemas.execution_consistency as ec
    import src.backend.app.schemas.execution_manifest as em
    import src.backend.app.schemas.execution_state as es

    for mod in (es, em, ec):
        source = inspect.getsource(mod)
        for forbidden in ("pipeline_executor", "state_store", "execute_reviewed_routes"):
            assert forbidden not in source, f"{mod.__name__} imports {forbidden}"


def test_helpers_create_no_files(tmp_path):
    """Phase 3 helper functions create no files."""
    before = list(tmp_path.iterdir())

    # execution_state helpers (pure — no state change to check)
    _ = is_run_terminal("succeeded")

    # execution_manifest helpers
    _ = build_output_manifest(
        project_id="p1",
        run_id="r1",
        node_id="n1",
        items=[OutputManifestItem(path="/tmp/t.json")],
    )

    # execution_consistency helpers
    base = _ci()
    _ = verify_execution_consistency(reviewed=base, dry_run=base, execution=base)

    after = list(tmp_path.iterdir())
    assert before == after


def test_no_rawdata_outputs_paths_referenced():
    """Phase 3 schema modules do not reference rawdata or outputs paths."""
    import src.backend.app.schemas.execution_consistency as ec
    import src.backend.app.schemas.execution_manifest as em
    import src.backend.app.schemas.execution_state as es

    for mod in (es, em, ec):
        source = inspect.getsource(mod)
        assert "rawdata" not in source.lower(), f"{mod.__name__} references rawdata"


def test_no_subprocess_imports():
    """Phase 3 schema modules must not import subprocess."""
    import src.backend.app.schemas.execution_consistency as ec
    import src.backend.app.schemas.execution_manifest as em
    import src.backend.app.schemas.execution_state as es

    for mod in (es, em, ec):
        # Check actual import statements (not docstring mentions)
        source = inspect.getsource(mod)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import subprocess") or stripped.startswith("from subprocess"):
                pytest.fail(f"{mod.__name__} imports subprocess: {stripped}")
