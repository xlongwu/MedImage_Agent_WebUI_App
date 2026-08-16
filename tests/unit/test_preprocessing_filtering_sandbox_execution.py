"""Tests for Temporal Filtering sandbox execution — Phase 5L."""

from __future__ import annotations

from pathlib import Path
def _setup(tmp_path, monkeypatch):
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    store = SQLiteDesktopStore(tmp_path / "db.sqlite")
    monkeypatch.setattr(
        "src.backend.app.services.preprocessing_filtering_execution.mock_store", store
    )
    return store


def _make_func(tmp_path):
    so = tmp_path / "preprocessing_runs" / "pp-test" / "spm_exec" / "nr-ex" / "sandbox_output"
    sub = so / "sub-001"
    sub.mkdir(parents=True)
    (sub / "residual_ssub-001_task-rest_bold.nii").write_text("residual")
    return so


_ALL = {
    "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
    "MEDIMAGE_ALLOW_SANDBOXED_TEMPORAL_FILTERING": "1",
}

from src.backend.app.schemas.preprocessing_filtering_execution import (  # noqa: E402
    FilteringSandboxExecutionRequest,
)
from src.backend.app.services.preprocessing_filtering_execution import (  # noqa: E402
    run_filtering_sandbox_execution,
)


def test_disabled_without_env(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = run_filtering_sandbox_execution(
        "test",
        "pp-test",
        FilteringSandboxExecutionRequest(dry_run_id="dr"),
        env={},
        project_dir=str(tmp_path),
    )
    assert result.status == "disabled"


def test_missing_dry_run_blocks(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = run_filtering_sandbox_execution(
        "brain-tumor-study",
        "pp-test",
        FilteringSandboxExecutionRequest(dry_run_id="nonexistent"),
        env=_ALL,
        project_dir=str(tmp_path),
    )
    assert result.status == "blocked"


def test_sandbox_copies_and_writes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    func_dir = _make_func(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "filtering_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    import builtins

    ri = builtins.__import__

    def mi(name, *a, **kw):
        if name in ("scipy", "scipy.signal"):
            raise ImportError("mock")
        return ri(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", mi)
    req = FilteringSandboxExecutionRequest(
        dry_run_id="dr-test", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_filtering_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok and Path(result.manifest_path).exists()
