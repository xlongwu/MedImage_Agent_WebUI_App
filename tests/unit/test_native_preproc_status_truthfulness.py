from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np

from src.backend.app.schemas.native_preproc_api import (
    NativeFullPreprocConfirmations,
    NativeFullPreprocRequest,
)
from src.backend.app.services.native_preproc_full import (
    get_native_full_report,
    get_native_full_run,
    get_native_full_validation,
    run_native_full_execute,
)


def _synthetic_native_inputs(root: Path) -> dict[str, str]:
    func = root / "converted_bids" / "sub-001" / "func"
    anat = root / "converted_bids" / "sub-001" / "anat"
    resources = root / "resources"
    func.mkdir(parents=True)
    anat.mkdir(parents=True)
    resources.mkdir()
    tr = 2.0
    n_timepoints = 48
    time = np.arange(n_timepoints, dtype=np.float32) * tr
    data = np.zeros((4, 4, 4, n_timepoints), dtype=np.float32) + 10.0
    data[:2, :, :, :] += np.sin(2 * np.pi * 0.03 * time)
    data[2:, :, :, :] += np.cos(2 * np.pi * 0.04 * time)
    bold = func / "sub-001_task-rest_bold.nii.gz"
    nib.save(nib.Nifti1Image(data, np.eye(4)), str(bold))
    sidecar = func / "sub-001_task-rest_bold.json"
    sidecar.write_text(
        json.dumps({"RepetitionTime": tr, "SliceTiming": [0.0, 0.5, 1.0, 1.5]}),
        encoding="utf-8",
    )

    t1 = np.zeros((4, 4, 4), dtype=np.float32)
    t1[:2] = 40.0
    t1[2:3] = 80.0
    t1[3:] = 120.0
    t1w = anat / "sub-001_T1w.nii.gz"
    nib.save(nib.Nifti1Image(t1, np.eye(4)), str(t1w))
    template_shape = (5, 4, 4)
    template_affine = np.eye(4, dtype=np.float32)
    template = resources / "template.nii.gz"
    nib.save(
        nib.Nifti1Image(np.ones(template_shape, dtype=np.float32), template_affine),
        str(template),
    )

    atlas_data = np.zeros(template_shape, dtype=np.int16)
    atlas_data[:2] = 1
    atlas_data[2:] = 2
    atlas = resources / "atlas.nii.gz"
    nib.save(nib.Nifti1Image(atlas_data, template_affine), str(atlas))
    labels = resources / "labels.tsv"
    labels.write_text("label\tname\n1\tSinROI\n2\tCosROI\n", encoding="utf-8")
    return {
        "bold": str(bold),
        "sidecar": str(sidecar),
        "t1w": str(t1w),
        "template": str(template),
        "atlas": str(atlas),
        "labels": str(labels),
    }


def _confirmations() -> NativeFullPreprocConfirmations:
    return NativeFullPreprocConfirmations(
        confirm_reviewed_native_execution=True,
        confirm_rawdata_readonly=True,
        confirm_no_external_tools=True,
        confirm_research_use_only=True,
        confirm_no_clinical_use=True,
    )


def test_native_full_execute_generates_truthful_artifact_backed_status(tmp_path) -> None:
    inputs = _synthetic_native_inputs(tmp_path)
    request = NativeFullPreprocRequest(
        run_id="native-synthetic",
        subject_id="sub-001",
        input_bold=inputs["bold"],
        sidecar_json=inputs["sidecar"],
        t1w=inputs["t1w"],
        template=inputs["template"],
        atlas=inputs["atlas"],
        atlas_labels=inputs["labels"],
        remove_first=2,
        confirmations=_confirmations(),
    )

    result = run_native_full_execute("brain-tumor-study", request, project_dir=str(tmp_path))

    assert result.ok is True
    assert result.status == "succeeded"
    assert not result.blocked_stages
    assert not result.failed_stages
    assert result.artifact_count >= 20
    assert result.safety_flags["no_external_tools_executed"] is True
    assert result.safety_flags["third_party_runtime_not_used"] is True
    fc = next(
        stage for stage in result.stage_results if stage.stage_id == "functional_connectivity"
    )
    assert fc.status == "warning"
    assert any(warning.startswith("gpu_fallback:") for warning in fc.warnings)
    assert {artifact["artifact_type"] for artifact in fc.output_artifacts} >= {
        "fc_matrix",
        "fisher_z_matrix",
    }
    for stage in result.stage_results:
        for artifact in stage.output_artifacts:
            path = Path(str(artifact["path"]))
            assert path.exists()
            assert path.stat().st_size > 0

    loaded = get_native_full_run("brain-tumor-study", "native-synthetic", project_dir=str(tmp_path))
    assert loaded.status == "succeeded"
    validation = get_native_full_validation(
        "brain-tumor-study",
        "native-synthetic",
        project_dir=str(tmp_path),
    )
    report = get_native_full_report(
        "brain-tumor-study",
        "native-synthetic",
        project_dir=str(tmp_path),
    )
    assert validation["ok"] is True
    assert report["ok"] is True
    assert Path(str(validation["validation_report_path"])).exists()
    assert Path(str(report["final_report_path"])).exists()


def test_native_full_execute_does_not_claim_computed_when_input_is_missing(tmp_path) -> None:
    request = NativeFullPreprocRequest(
        run_id="native-missing-input",
        input_bold=str(tmp_path / "missing_bold.nii.gz"),
        confirmations=_confirmations(),
    )

    result = run_native_full_execute("brain-tumor-study", request, project_dir=str(tmp_path))

    assert result.ok is False
    assert result.status == "blocked"
    assert "input_validation" in result.blocked_stages
    assert result.artifact_count == 0
    assert not result.completed_stages
