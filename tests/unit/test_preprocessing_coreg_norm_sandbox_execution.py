"""Tests for coregistration + normalization sandbox execution — Phase 5H."""

from __future__ import annotations

from pathlib import Path
def _setup_store(tmp_path, monkeypatch):
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    store = SQLiteDesktopStore(tmp_path / "db.sqlite")
    monkeypatch.setattr(
        "src.backend.app.services.preprocessing_coreg_norm_execution.mock_store", store
    )
    return store
def _make_inputs(tmp_path):
    sandbox = tmp_path / "preprocessing_runs" / "pp-test" / "spm_exec" / "ex-1" / "sandbox_output"
    sub = sandbox / "sub-001"
    sub.mkdir(parents=True)
    (sub / "rasub-001_task-rest_bold.nii").write_text("func")
    cb = tmp_path / "cb"
    sub2 = cb / "sub-001" / "anat"
    sub2.mkdir(parents=True)
    (sub2 / "sub-001_T1w.nii.gz").write_text("t1w")
    return sandbox, cb


_ALL_FLAGS = {
    "MEDIMAGE_MATLAB_ENABLED": "1",
    "MEDIMAGE_SPM_SMOKE_ENABLED": "1",
    "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
    "MEDIMAGE_ALLOW_SANDBOXED_SPM_PREPROCESSING": "1",
    "MEDIMAGE_ALLOW_SANDBOXED_SPM_COREG_NORM": "1",
}

import subprocess as _sp  # noqa: E402

from src.backend.app.schemas.preprocessing_coreg_norm_execution import (  # noqa: E402
    CoregNormSandboxExecutionRequest,
)
from src.backend.app.services.preprocessing_coreg_norm_execution import (  # noqa: E402
    run_coreg_norm_sandbox_execution,
)


def test_disabled_without_env(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    result = run_coreg_norm_sandbox_execution(
        "test",
        "pp-test",
        CoregNormSandboxExecutionRequest(dry_run_id="dr-test"),
        env={},
        project_dir=str(tmp_path),
    )
    assert result.status == "disabled"


def test_missing_dry_run_blocks(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    result = run_coreg_norm_sandbox_execution(
        "brain-tumor-study",
        "pp-test",
        CoregNormSandboxExecutionRequest(dry_run_id="nonexistent"),
        env=_ALL_FLAGS,
        project_dir=str(tmp_path),
    )
    assert result.status == "blocked"


def test_sandbox_copies(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    sandbox, cb = _make_inputs(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "coreg_norm_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    req = CoregNormSandboxExecutionRequest(
        dry_run_id="dr-test",
        functional_input_dir=str(sandbox),
        t1w_input_dir=str(cb),
        confirm_sandbox_copy=True,
    )
    result = run_coreg_norm_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert result.ok and Path(result.sandbox_input_dir).exists()


def test_manifest_provenance(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    sandbox, cb = _make_inputs(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "coreg_norm_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    req = CoregNormSandboxExecutionRequest(
        dry_run_id="dr-test",
        functional_input_dir=str(sandbox),
        t1w_input_dir=str(cb),
        confirm_sandbox_copy=True,
    )
    result = run_coreg_norm_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert Path(result.manifest_path).exists()


def test_failure_logs(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    sandbox, cb = _make_inputs(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "coreg_norm_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "err"})(),
    )
    req = CoregNormSandboxExecutionRequest(
        dry_run_id="dr-test",
        functional_input_dir=str(sandbox),
        t1w_input_dir=str(cb),
        confirm_sandbox_copy=True,
    )
    result = run_coreg_norm_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert result.status == "failed" and Path(result.stderr_log_path).read_text() == "err"


def test_safety_flags(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    sandbox, cb = _make_inputs(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "coreg_norm_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    req = CoregNormSandboxExecutionRequest(
        dry_run_id="dr-test",
        functional_input_dir=str(sandbox),
        t1w_input_dir=str(cb),
        confirm_sandbox_copy=True,
    )
    result = run_coreg_norm_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert (
        result.safety_flags["no_dpabi"] is True and result.safety_flags["coreg_norm_only"] is True
    )
