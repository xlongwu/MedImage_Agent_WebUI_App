"""Tests for Nuisance sandbox execution — Phase 5K."""

from __future__ import annotations

from pathlib import Path
def _setup(tmp_path, monkeypatch):
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    store = SQLiteDesktopStore(tmp_path / "db.sqlite")
    monkeypatch.setattr(
        "src.backend.app.services.preprocessing_nuisance_execution.mock_store", store
    )
    return store


def _make_func(tmp_path):
    so = tmp_path / "preprocessing_runs" / "pp-test" / "spm_exec" / "s-ex" / "sandbox_output"
    sub = so / "sub-001"
    sub.mkdir(parents=True)
    (sub / "ssub-001_task-rest_bold.nii").write_text("smooth")
    (sub / "rp_sub-001.txt").write_text("motion")
    return so


_ALL = {
    "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
    "MEDIMAGE_ALLOW_SANDBOXED_NUISANCE_REGRESSION": "1",
}

from src.backend.app.schemas.preprocessing_nuisance_execution import (  # noqa: E402
    NuisanceSandboxExecutionRequest,  # noqa: E402
)
from src.backend.app.services.preprocessing_nuisance_execution import (  # noqa: E402
    run_nuisance_sandbox_execution,  # noqa: E402
)


def test_disabled_without_env(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    req = NuisanceSandboxExecutionRequest(dry_run_id="dr-test")
    result = run_nuisance_sandbox_execution(
        "test", "pp-test", req, env={}, project_dir=str(tmp_path)
    )
    assert result.status == "disabled"


def test_missing_dry_run_blocks(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = run_nuisance_sandbox_execution(
        "brain-tumor-study",
        "pp-test",
        NuisanceSandboxExecutionRequest(dry_run_id="nonexistent"),
        env=_ALL,
        project_dir=str(tmp_path),
    )
    assert result.status == "blocked"


def test_sandbox_copies_and_writes_designs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    func_dir = _make_func(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "nuisance_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    req = NuisanceSandboxExecutionRequest(
        dry_run_id="dr-test", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_nuisance_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok and Path(result.manifest_path).exists()


def test_metadata_only_warning(tmp_path, monkeypatch):
    # Force nibabel import to fail so metadata-only path is taken
    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "nibabel":
            raise ImportError("Mocked: nibabel not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    _setup(tmp_path, monkeypatch)
    func_dir = _make_func(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "nuisance_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    req = NuisanceSandboxExecutionRequest(
        dry_run_id="dr-test", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_nuisance_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.status == "warning" and any(
        "nibabel" in str(w).lower() or "metadata-only" in str(w).lower() for w in result.warnings
    )
