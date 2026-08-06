from __future__ import annotations

from pathlib import Path

import numpy as np

from src.backend.app.native_preproc.core.acpc import AcpcGeometryError, construct_acpc_frame, is_right_handed_rigid
from src.backend.app.native_preproc.io.nifti_io import load_nifti, save_nifti
from src.backend.app.native_preproc.stages.acpc_alignment import load_acpc_reference, run_acpc_alignment


def test_acpc_frame_is_right_handed_and_places_ac_at_origin() -> None:
    frame = construct_acpc_frame(
        np.array((10.0, 20.0, 30.0)),
        np.array((10.0, -4.0, 30.0)),
        np.array((1.0, 0.0, 0.0)),
    )
    assert is_right_handed_rigid(frame)
    assert frame[:3, 3].tolist() == [10.0, 20.0, 30.0]


def test_acpc_frame_rejects_degenerate_ac_pc_line() -> None:
    with np.testing.assert_raises(AcpcGeometryError):
        construct_acpc_frame(np.zeros(3), np.zeros(3), np.array((1.0, 0.0, 0.0)))


def test_bundled_spm_reference_is_checksum_pinned() -> None:
    reference = load_acpc_reference()
    assert reference.template_path.is_file()
    assert len(reference.template_sha256) == 64
    assert reference.ac_mm.tolist() == [0.0, 0.0, 0.0]
    assert reference.pc_mm.tolist() == [0.0, -24.0, 0.0]


def test_acpc_stage_writes_reloadable_estimated_outputs(tmp_path: Path) -> None:
    reference = load_acpc_reference()
    result = run_acpc_alignment(reference.template_path, tmp_path, run_id="acpc-test")
    assert result.status == "succeeded"
    assert result.capability_level == "computed"
    assert result.qc.metrics["review_required"] is False
    output_types = {artifact.artifact_type for artifact in result.output_artifacts}
    assert {"acpc_t1w", "transform_matrix", "acpc_landmarks", "qc_json"}.issubset(output_types)
    aligned = next(artifact for artifact in result.output_artifacts if artifact.artifact_type == "acpc_t1w")
    assert load_nifti(aligned.path).data.ndim == 3


def test_acpc_stage_fails_closed_for_an_empty_t1w(tmp_path: Path) -> None:
    source = tmp_path / "empty_T1w.nii"
    save_nifti(source, np.zeros((9, 9, 9), dtype=np.float32), np.eye(4), dtype=np.float32)

    result = run_acpc_alignment(source, tmp_path / "derivatives", run_id="acpc-empty")

    assert result.status == "failed"
    assert result.output_artifacts == []
    assert result.qc.metrics["review_required"] is True
    assert not list((tmp_path / "derivatives").rglob("*_desc-acpc_landmarks.json"))
