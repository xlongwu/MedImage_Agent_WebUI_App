"""Tests for ALFF/ReHo sandbox execution — Phase 5M."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _setup(tmp_path, monkeypatch):
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    store = SQLiteDesktopStore(tmp_path / "db.sqlite")
    monkeypatch.setattr(
        "src.backend.app.services.preprocessing_alff_reho_execution.mock_store", store
    )
    return store


def _make_func(tmp_path):
    so = tmp_path / "preprocessing_runs" / "pp-test" / "spm_exec" / "tf-ex" / "sandbox_output"
    sub = so / "sub-001"
    sub.mkdir(parents=True)
    (sub / "filtered_sub-001_task-rest_bold.nii").write_text("filtered")
    return so


_ALL = {"MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1", "MEDIMAGE_ALLOW_SANDBOXED_ALFF_REHO": "1"}

from src.backend.app.schemas.preprocessing_alff_reho_execution import (  # noqa: E402
    AlffRehoSandboxExecutionRequest,
)
from src.backend.app.services.preprocessing_alff_reho_execution import (  # noqa: E402
    bids_sidecar_for,
    run_alff_reho_sandbox_execution,
)


def test_disabled_without_env(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = run_alff_reho_sandbox_execution(
        "test",
        "pp-test",
        AlffRehoSandboxExecutionRequest(dry_run_id="dr"),
        env={},
        project_dir=str(tmp_path),
    )
    assert result.status == "disabled"


def test_missing_dry_run_blocks(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = run_alff_reho_sandbox_execution(
        "brain-tumor-study",
        "pp-test",
        AlffRehoSandboxExecutionRequest(dry_run_id="nonexistent"),
        env=_ALL,
        project_dir=str(tmp_path),
    )
    assert result.status == "blocked"


def test_metadata_first_exec_writes_plans(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    func_dir = _make_func(tmp_path)
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / "dr-test"
    dd.mkdir(parents=True)
    (dd / "alff_reho_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    import builtins

    ri = builtins.__import__

    def mi(name, *a, **kw):
        if name in ("nibabel", "numpy"):
            raise ImportError("mock")
        return ri(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", mi)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-test", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "brain-tumor-study", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok and Path(result.metric_plan_path).exists()


# ── P0-1 / P0-2 / P1-5: TR reading, sidecar copy, partial status, file naming ──


def test_bids_sidecar_for_nii_gz():
    """bids_sidecar_for correctly strips .nii.gz → .json (BIDS spec)."""
    p = Path("data/sub-001/func/sub-001_task-rest_bold.nii.gz")
    sc = bids_sidecar_for(p)
    assert sc is not None
    assert sc.name == "sub-001_task-rest_bold.json"
    assert sc.parent == Path("data/sub-001/func")


def test_bids_sidecar_for_nii():
    """bids_sidecar_for correctly strips .nii → .json."""
    p = Path("data/sub-001_task-rest_bold.nii")
    sc = bids_sidecar_for(p)
    assert sc is not None
    assert sc.name == "sub-001_task-rest_bold.json"


def test_bids_sidecar_for_non_nifti():
    """bids_sidecar_for returns None for non-NIfTI files."""
    assert bids_sidecar_for(Path("data/file.dcm")) is None
    assert bids_sidecar_for(Path("data/file.json")) is None


def _make_synth_bold(func_dir, tr=1.5, with_sidecar=True):
    """Create a synthetic 4D BOLD NIfTI + optional BIDS JSON sidecar.

    Returns the path to the .nii.gz file.
    """
    np = pytest.importorskip("numpy")
    nib = pytest.importorskip("nibabel")
    sub_dir = func_dir / "sub-001" / "func"
    sub_dir.mkdir(parents=True, exist_ok=True)
    bold_path = sub_dir / "sub-001_task-rest_bold.nii.gz"
    # 12x12x12x50 — enough timepoints for ALFF (T>=8) and ReHo (27-neighborhood)
    data = np.random.rand(12, 12, 12, 50).astype(np.float32)
    nib.save(nib.Nifti1Image(data, np.eye(4)), str(bold_path))
    if with_sidecar:
        sidecar = sub_dir / "sub-001_task-rest_bold.json"
        sidecar.write_text(json.dumps({"RepetitionTime": tr, "TaskName": "rest"}))
    return bold_path


def _make_many_synth_bold(func_dir, count=12, tr=2.0):
    np = pytest.importorskip("numpy")
    nib = pytest.importorskip("nibabel")
    for idx in range(1, count + 1):
        subject = f"sub-{idx:03d}"
        sub_dir = func_dir / subject / "func"
        sub_dir.mkdir(parents=True, exist_ok=True)
        bold_path = sub_dir / f"{subject}_task-rest_bold.nii.gz"
        data = np.ones((4, 4, 4, 8), dtype=np.float32) * idx
        nib.save(nib.Nifti1Image(data, np.eye(4)), str(bold_path))
        (sub_dir / f"{subject}_task-rest_bold.json").write_text(
            json.dumps({"RepetitionTime": tr, "TaskName": "rest"})
        )


def _make_dry_run(tmp_path, dry_run_id="dr-synth"):
    dd = tmp_path / "preprocessing_runs" / "pp-test" / "spm_dry_runs" / dry_run_id
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "alff_reho_dry_run_manifest.json").write_text('{"status":"dry_run_preview"}')
    return dd


def test_sidecar_copied_to_sandbox(tmp_path, monkeypatch):
    """BIDS JSON sidecar is copied into sandbox_input alongside NIfTI."""
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_synth_bold(func_dir, tr=1.5, with_sidecar=True)
    _make_dry_run(tmp_path)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "proj", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok
    # Sidecar should exist in sandbox_input
    sidecars = list(Path(result.sandbox_input_dir).rglob("*.json"))
    assert len(sidecars) >= 1, "BIDS JSON sidecar not copied to sandbox_input"


def test_tr_source_recorded_in_provenance(tmp_path, monkeypatch):
    """provenance.json contains tr_source field."""
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_synth_bold(func_dir, tr=1.5, with_sidecar=True)
    _make_dry_run(tmp_path)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "proj", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok
    prov = json.loads(Path(result.provenance_path).read_text())
    assert "tr_source" in prov
    assert prov["tr_source"] == "bids_json"
    assert result.tr_source == "bids_json"


def test_default_tr_adds_warning(tmp_path, monkeypatch):
    """Missing sidecar → tr_source='default' + warning in response."""
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_synth_bold(func_dir, with_sidecar=False)  # no sidecar
    _make_dry_run(tmp_path)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "proj", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok
    assert result.tr_source == "default"
    tr_warnings = [w for w in result.warnings if "TR sidecar not found" in w]
    assert len(tr_warnings) >= 1, "Expected TR fallback warning"


def test_no_sub_sub_prefix(tmp_path, monkeypatch):
    """Output filenames use 'sub-001_desc-...' not 'sub-sub-001_desc-...'."""
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_synth_bold(func_dir, tr=2.0, with_sidecar=True)
    _make_dry_run(tmp_path)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "proj", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok
    alff_maps = list(Path(result.sandbox_output_dir).rglob("*desc-alff_map.nii.gz"))
    assert len(alff_maps) >= 1
    # Filename uses subject prefix, directory uses full BIDS prefix
    # Must be sub-001_desc-alff_map.nii.gz, NOT sub-sub-001_desc-alff_map.nii.gz
    assert all("sub-sub-" not in p.name for p in alff_maps), (
        f"Found double sub- prefix: {[p.name for p in alff_maps]}"
    )
    assert any(p.name == "sub-001_desc-alff_map.nii.gz" for p in alff_maps)


def test_succeeded_all_metrics(tmp_path, monkeypatch):
    """All metrics succeed → status='succeeded', subjects_succeeded=1."""
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_synth_bold(func_dir, tr=2.0, with_sidecar=True)
    _make_dry_run(tmp_path)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "proj", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok
    assert result.status == "succeeded", (
        f"Expected succeeded, got {result.status}: {result.warnings}"
    )
    assert result.subjects_succeeded == 1
    assert result.subjects_partial == 0
    assert result.alff_computed is True
    assert result.reho_computed is True


def test_default_processes_all_files_and_preview_limit_is_explicit(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_many_synth_bold(func_dir, count=12, tr=2.0)
    _make_dry_run(tmp_path)

    def mock_alff(data, *a, **kw):
        return {
            "ok": True,
            "backend": "cpu-numpy",
            "alff": np.zeros(data.shape[:3], dtype=np.float32),
            "falff": np.zeros(data.shape[:3], dtype=np.float32),
            "errors": [],
        }

    def mock_reho(data, *a, **kw):
        return {
            "ok": True,
            "backend": "cpu-numpy",
            "reho": np.zeros(data.shape[:3], dtype=np.float32),
            "valid_voxel_count": int(np.prod(data.shape[:3])),
            "errors": [],
        }

    monkeypatch.setattr("src.backend.app.tools.alff_compute.compute_alff_backend", mock_alff)
    monkeypatch.setattr("src.backend.app.tools.reho_compute.compute_reho_backend", mock_reho)

    full_req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    full = run_alff_reho_sandbox_execution(
        "proj", "pp-test", full_req, env=_ALL, project_dir=str(tmp_path)
    )
    assert full.ok
    assert full.files_discovered == 12
    assert full.files_selected == 12
    assert full.dataset_complete is True
    assert full.status == "succeeded"

    preview_req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth",
        functional_input_dir=str(func_dir),
        confirm_sandbox_copy=True,
        preview_limit=3,
    )
    preview = run_alff_reho_sandbox_execution(
        "proj", "pp-test", preview_req, env=_ALL, project_dir=str(tmp_path)
    )
    assert preview.ok
    assert preview.files_discovered == 12
    assert preview.files_selected == 3
    assert preview.dataset_complete is False
    assert preview.status == "partial"
    provenance = json.loads(Path(preview.provenance_path).read_text())
    assert provenance["dataset_selection"]["selection_policy"] == "explicit_preview_limit"


def test_partial_status_alff_only(tmp_path, monkeypatch):
    """ALFF succeeds but ReHo fails → status='partial'."""
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_synth_bold(func_dir, tr=2.0, with_sidecar=True)
    _make_dry_run(tmp_path)

    # Mock ReHo kernel to fail
    def mock_reho_fail(*a, **kw):
        return {"ok": False, "errors": ["mocked ReHo failure"]}

    monkeypatch.setattr("src.backend.app.tools.reho_compute.compute_reho_backend", mock_reho_fail)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "proj", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok
    assert result.status == "partial", f"Expected partial, got {result.status}"
    assert result.alff_computed is True
    assert result.reho_computed is False
    assert result.subjects_partial == 1
    assert result.subjects_succeeded == 0


def test_partial_status_reho_only(tmp_path, monkeypatch):
    """ReHo succeeds but ALFF fails → status='partial'."""
    _setup(tmp_path, monkeypatch)
    func_dir = tmp_path / "func_input"
    func_dir.mkdir()
    _make_synth_bold(func_dir, tr=2.0, with_sidecar=True)
    _make_dry_run(tmp_path)

    # Mock ALFF kernel to fail
    def mock_alff_fail(*a, **kw):
        return {"ok": False, "errors": ["mocked ALFF failure"]}

    monkeypatch.setattr("src.backend.app.tools.alff_compute.compute_alff_backend", mock_alff_fail)
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id="dr-synth", functional_input_dir=str(func_dir), confirm_sandbox_copy=True
    )
    result = run_alff_reho_sandbox_execution(
        "proj", "pp-test", req, env=_ALL, project_dir=str(tmp_path)
    )
    assert result.ok
    assert result.status == "partial", f"Expected partial, got {result.status}"
    assert result.alff_computed is False
    assert result.reho_computed is True
    assert result.subjects_partial == 1
    assert result.subjects_succeeded == 0
