"""Tests for SPM sandbox slice timing + realign execution — Phase 5E."""

from __future__ import annotations

import json
from pathlib import Path
def _make_bold_input(tmp_path: Path, subjects: int = 2) -> Path:
    cb = tmp_path / "converted_bids"
    for i in range(1, subjects + 1):
        sub = cb / f"sub-{i:03d}" / "func"
        sub.mkdir(parents=True)
        (sub / f"sub-{i:03d}_task-rest_bold.nii.gz").write_text("fake BOLD")
        (sub / f"sub-{i:03d}_task-rest_bold.json").write_text('{"RepetitionTime":2.0}')
    return cb
def _setup_store(tmp_path, monkeypatch):
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    store = SQLiteDesktopStore(tmp_path / "db.sqlite")
    monkeypatch.setattr("src.backend.app.services.preprocessing_spm_execution.mock_store", store)
    return store


_ALL_FLAGS = {
    "MEDIMAGE_MATLAB_ENABLED": "1",
    "MEDIMAGE_SPM_SMOKE_ENABLED": "1",
    "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
    "MEDIMAGE_ALLOW_SANDBOXED_SPM_PREPROCESSING": "1",
}


# ═══════════════════════════════════════════════════════════════════════
# Group 1 — Env flag gating
# ═══════════════════════════════════════════════════════════════════════


def test_disabled_without_env_flags(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(dry_run_id="dr-test")
    result = run_sandbox_spm_execution("test", "pp-test", req, env={}, project_dir=str(tmp_path))
    assert result.status == "disabled"


def test_env_validator():
    from src.backend.app.schemas.preprocessing_spm_execution import validate_sandbox_env

    ok, missing = validate_sandbox_env({})
    assert not ok
    assert len(missing) == 4


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Sandbox execution
# ═══════════════════════════════════════════════════════════════════════


def test_missing_dry_run_blocks(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(
        dry_run_id="nonexistent", preprocessing_input_dir=str(tmp_path)
    )
    result = run_sandbox_spm_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert result.status == "blocked"


def test_sandbox_copies_bold_files(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    cb = _make_bold_input(tmp_path)
    # Create a fake dry-run first
    dry_dir = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dry_dir.mkdir(parents=True)
    (dry_dir / "dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    # Patch subprocess to avoid real MATLAB call
    import subprocess as _sp

    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(
        dry_run_id="dr-test", preprocessing_input_dir=str(cb), confirm_sandbox_copy=True
    )
    result = run_sandbox_spm_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert result.ok
    assert result.subjects_total == 2
    assert Path(result.sandbox_input_dir).exists()
    assert len(list(Path(result.sandbox_input_dir).rglob("*.nii*"))) == 2


def test_sandbox_default_processes_all_bold_files(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    cb = _make_bold_input(tmp_path, subjects=12)
    dry_dir = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dry_dir.mkdir(parents=True)
    (dry_dir / "dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    import subprocess as _sp

    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(
        dry_run_id="dr-test", preprocessing_input_dir=str(cb), confirm_sandbox_copy=True
    )
    result = run_sandbox_spm_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert result.status == "succeeded"
    assert result.subjects_discovered == 12
    assert result.subjects_selected == 12
    assert len(list(Path(result.sandbox_input_dir).rglob("*.nii*"))) == 12
    assert manifest["dataset_selection"]["selection_policy"] == "all"
    assert manifest["dataset_selection"]["preview_only"] is False


def test_sandbox_preview_limit_marks_preview_only(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    cb = _make_bold_input(tmp_path, subjects=12)
    dry_dir = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dry_dir.mkdir(parents=True)
    (dry_dir / "dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    import subprocess as _sp

    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(
        dry_run_id="dr-test",
        preprocessing_input_dir=str(cb),
        confirm_sandbox_copy=True,
        preview_limit=3,
    )
    result = run_sandbox_spm_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    subject_status = json.loads(Path(result.subject_status_path).read_text(encoding="utf-8"))
    assert result.status == "preview_only"
    assert result.preview_only is True
    assert result.partial is True
    assert result.subjects_discovered == 12
    assert result.subjects_selected == 3
    assert len(list(Path(result.sandbox_input_dir).rglob("*.nii*"))) == 3
    assert manifest["dataset_selection"]["selection_policy"] == "explicit_preview_limit"
    assert manifest["dataset_selection"]["preview_only"] is True
    assert subject_status["preview_only"] is True
    assert subject_status["partial"] is True


def test_sandbox_writes_manifest_provenance(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    cb = _make_bold_input(tmp_path, subjects=1)
    dry_dir = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dry_dir.mkdir(parents=True)
    (dry_dir / "dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    import subprocess as _sp

    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(
        dry_run_id="dr-test", preprocessing_input_dir=str(cb), confirm_sandbox_copy=True
    )
    result = run_sandbox_spm_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert Path(result.manifest_path).exists()
    assert Path(result.provenance_path).exists()
    assert Path(result.stdout_log_path).exists()


def test_sandbox_failure_writes_logs(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    cb = _make_bold_input(tmp_path, subjects=1)
    dry_dir = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dry_dir.mkdir(parents=True)
    (dry_dir / "dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    import subprocess as _sp

    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})(),
    )
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(
        dry_run_id="dr-test", preprocessing_input_dir=str(cb), confirm_sandbox_copy=True
    )
    result = run_sandbox_spm_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert result.status == "failed"
    assert "error" in Path(result.stderr_log_path).read_text()


def test_sandbox_safety_flags(tmp_path, monkeypatch):
    _setup_store(tmp_path, monkeypatch)
    cb = _make_bold_input(tmp_path, subjects=1)
    dry_dir = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dry_dir.mkdir(parents=True)
    (dry_dir / "dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    import subprocess as _sp

    monkeypatch.setattr(
        _sp,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})(),
    )
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    req = SpmSandboxExecutionRequest(
        dry_run_id="dr-test", preprocessing_input_dir=str(cb), confirm_sandbox_copy=True
    )
    result = run_sandbox_spm_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL_FLAGS, project_dir=str(tmp_path)
    )
    assert result.safety_flags["sandbox_execution_only"] is True
    assert result.safety_flags["no_full_preprocessing"] is True
    assert result.safety_flags["no_dpabi"] is True
