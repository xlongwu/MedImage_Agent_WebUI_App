from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

nib = pytest.importorskip("nibabel")

from src.backend.app.native_preproc.orchestrator.validation import (  # noqa: E402
    validate_stage_result_artifacts,  # noqa: E402
)
from src.backend.app.native_preproc.stages.nuisance_regression import (  # noqa: E402
    run_nuisance_regression,  # noqa: E402
)


def _save_bold(path: Path, data: np.ndarray) -> Path:
    nib.save(nib.Nifti1Image(data.astype(np.float32), affine=np.eye(4)), str(path))
    return path


def _write_motion(path: Path, motion: np.ndarray) -> Path:
    header = "trans_x\ttrans_y\ttrans_z\trot_x\trot_y\trot_z"
    rows = ["\t".join(f"{float(value):.8f}" for value in row) for row in motion]
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(a.reshape(-1), b.reshape(-1))[0, 1])


def test_nuisance_regression_removes_known_motion_confound_and_writes_tsv(tmp_path: Path) -> None:
    timepoints = 48
    t = np.linspace(0.0, 2.0 * np.pi, timepoints, dtype=np.float32)
    confound = np.sin(t)
    signal = np.cos(3.0 * t)
    data = np.zeros((2, 2, 2, timepoints), dtype=np.float32)
    data[:] = signal + 3.0 * confound
    bold = _save_bold(tmp_path / "sub-01_task-rest_bold.nii.gz", data)
    motion = np.zeros((timepoints, 6), dtype=np.float32)
    motion[:, 0] = confound
    motion_path = _write_motion(tmp_path / "rp_sub-01.tsv", motion)

    result = run_nuisance_regression(
        bold,
        tmp_path / "native",
        motion_parameters=motion_path,
        motion_model="motion6",
        polynomial_order=0,
    )

    assert result.status == "warning"
    assert any(warning.startswith("gpu_fallback:") for warning in result.warnings)
    assert validate_stage_result_artifacts(result) == []
    residual_path = Path(
        next(
            artifact.path
            for artifact in result.output_artifacts
            if artifact.artifact_type == "residual_bold"
        )
    )
    confounds_path = Path(
        next(
            artifact.path
            for artifact in result.output_artifacts
            if artifact.artifact_type == "confound_matrix"
        )
    )
    residual = np.asanyarray(nib.load(residual_path).dataobj)
    assert residual.shape == data.shape
    assert abs(_corr(residual[0, 0, 0, :], confound)) < 0.05
    assert abs(_corr(data[0, 0, 0, :], confound)) > 0.9
    assert (
        confounds_path.read_text(encoding="utf-8").splitlines()[0].startswith("intercept\ttrans_x")
    )
    assert result.qc.metrics["timepoints_preserved"] is True


def test_nuisance_regression_blocks_mask_mismatch_without_outputs(tmp_path: Path) -> None:
    bold = _save_bold(
        tmp_path / "sub-01_task-rest_bold.nii.gz", np.zeros((2, 2, 2, 4), dtype=np.float32)
    )
    mask = tmp_path / "wm.nii.gz"
    nib.save(nib.Nifti1Image(np.ones((3, 3, 3), dtype=np.float32), affine=np.eye(4)), str(mask))

    result = run_nuisance_regression(bold, tmp_path / "native", include_wm=True, wm_mask=mask)

    assert result.status == "blocked"
    assert result.output_artifacts == []
    assert "does not match BOLD spatial shape" in result.errors[0]
