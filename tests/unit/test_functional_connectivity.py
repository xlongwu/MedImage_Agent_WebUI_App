from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np

from src.backend.app.tools import functional_connectivity as fc_module
from src.backend.app.tools.functional_connectivity import run_python_functional_connectivity_subject


def test_python_fc_outputs_matrices(tmp_path: Path):
    d = tmp_path / "derivatives"
    sid = "sub-001"
    fd = d / "rsfmri_preproc" / sid / "func"
    fd.mkdir(parents=True)
    ip = fd / "filt_resid_swrasub-001_bold.nii"
    nt = 12
    t = np.linspace(0, 2 * np.pi, nt, dtype=np.float32)
    data = np.zeros((4, 4, 4, nt), dtype=np.float32)
    data[0:1, :, :, :] = np.sin(t)
    data[1:2, :, :, :] = np.sin(t)
    data[2:3, :, :, :] = np.cos(t)
    data[3:4, :, :, :] = -np.sin(t)
    nib.save(nib.Nifti1Image(data, affine=np.eye(4)), str(ip))
    r = run_python_functional_connectivity_subject(
        subject_id=sid, derivatives_dir=str(d), roi_count=4, generate_seed_map=True
    )
    assert r["ok"] is True
    fcd = d / "rsfmri_fc" / sid
    assert (fcd / "roi_timeseries.tsv").exists()
    assert (fcd / "correlation_matrix.tsv").exists()
    assert (fcd / "fisher_z_matrix.tsv").exists()
    assert (fcd / "seed_correlation_map.nii").exists()
    qp = d / "rsfmri_qc" / sid / "functional_connectivity_qc.json"
    assert qp.exists()
    pl = json.loads(qp.read_text(encoding="utf-8"))
    assert pl["subject_id"] == sid
    assert pl["fc_qc_status"] in {"PASS", "WARNING"}
    assert pl["roi_count"] == 4
    assert pl["correlation_matrix_shape"] == [4, 4]
    assert pl["stage_status"] == "preview_only"
    assert pl["preview_only"] is True
    assert (fcd / "correlation_matrix.npy").exists()
    assert (fcd / "fisher_z_matrix.npy").exists()
    assert (fcd / "functional_connectivity_provenance.json").exists()


def test_python_fc_reads_subject_derivative_from_an_approved_input_root(tmp_path: Path):
    input_derivatives = tmp_path / "parent-derivatives"
    output_derivatives = tmp_path / "recovery-derivatives"
    sid = "sub-003"
    input_dir = input_derivatives / "rsfmri_preproc" / sid / "func"
    input_dir.mkdir(parents=True)
    input_path = input_dir / "filt_smoke_sub-003_bold.nii.gz"
    data = np.zeros((4, 4, 4, 8), dtype=np.float32)
    nib.save(nib.Nifti1Image(data, affine=np.eye(4)), str(input_path))
    atlas_path = input_derivatives / "atlases" / "recovery-atlas.nii.gz"
    atlas_path.parent.mkdir(parents=True)
    nib.save(
        nib.Nifti1Image(np.ones((4, 4, 4), dtype=np.int16), affine=np.eye(4)),
        str(atlas_path),
    )

    result = run_python_functional_connectivity_subject(
        subject_id=sid,
        derivatives_dir=str(output_derivatives),
        input_nii=str(input_path),
        atlas_path=str(atlas_path),
        allowed_input_roots=(str(input_derivatives),),
    )

    assert result["ok"] is True, result["errors"]
    assert result["atlas_grounded"] is True
    assert Path(result["correlation_matrix_npy"]).is_relative_to(output_derivatives)


def test_python_fc_with_real_atlas_outputs_reloadable_grounded_artifacts(tmp_path: Path):
    d = tmp_path / "derivatives"
    sid = "sub-001"
    fd = d / "rsfmri_preproc" / sid / "func"
    fd.mkdir(parents=True)
    ip = fd / "filt_resid_rsub-001_bold.nii.gz"
    nt = 16
    t = np.linspace(0, 2 * np.pi, nt, dtype=np.float32)
    data = np.zeros((4, 4, 3, nt), dtype=np.float32)
    data[:2, :, :, :] = np.sin(t)
    data[2:, :, :, :] = np.cos(t)
    affine = np.eye(4)
    nib.save(nib.Nifti1Image(data, affine=affine), str(ip))
    atlas = np.zeros((4, 4, 3), dtype=np.int16)
    atlas[:2, :, :] = 1
    atlas[2:, :, :] = 2
    atlas_path = d / "atlases" / "subject_atlas.nii.gz"
    atlas_path.parent.mkdir(parents=True)
    nib.save(nib.Nifti1Image(atlas, affine=affine), str(atlas_path))
    labels_path = atlas_path.with_suffix("").with_suffix(".tsv")
    labels_path.write_text("label\tname\n1\tSin\n2\tCos\n", encoding="utf-8")

    result = run_python_functional_connectivity_subject(
        subject_id=sid,
        derivatives_dir=str(d),
        atlas_path=str(atlas_path),
        labels_path=str(labels_path),
    )

    assert result["ok"] is True, result["errors"]
    assert result["stage_status"] == "succeeded"
    assert result["atlas_grounded"] is True
    assert result["preview_only"] is False
    corr = np.load(fcd := Path(result["correlation_matrix_npy"]))
    fz = np.load(result["fisher_z_matrix_npy"])
    assert corr.shape == (2, 2)
    assert fz.shape == (2, 2)
    assert np.allclose(corr, corr.T, atol=1e-6)
    assert np.allclose(np.diag(corr), 1.0, atol=1e-6)
    assert np.allclose(np.diag(fz), 0.0, atol=1e-6)
    labels = json.loads(Path(result["labels_json"]).read_text(encoding="utf-8"))
    assert labels["labels"][0]["name"] == "Sin"
    provenance = json.loads(Path(result["provenance_json"]).read_text(encoding="utf-8"))
    assert provenance["atlas_grounded"] is True
    assert provenance["atlas_checksum"]
    assert fcd.exists()


def test_python_fc_materializes_known_template_atlas_before_execution(tmp_path: Path, monkeypatch):
    d = tmp_path / "derivatives"
    sid = "sub-001"
    fd = d / "rsfmri_preproc" / sid / "func"
    fd.mkdir(parents=True)
    ip = fd / "filt_resid_rsub-001_bold.nii"
    nt = 10
    t = np.linspace(0, 2 * np.pi, nt, dtype=np.float32)
    data = np.zeros((4, 4, 3, nt), dtype=np.float32)
    data[:2, :, :, :] = np.sin(t)
    data[2:, :, :, :] = np.cos(t)
    nib.save(nib.Nifti1Image(data, affine=np.eye(4)), str(ip))

    template_root = tmp_path / "repo_templates"
    template_root.mkdir()
    atlas = np.zeros((4, 4, 3), dtype=np.int16)
    atlas[:2, :, :] = 1
    atlas[2:, :, :] = 2
    template_atlas = template_root / "aal.nii"
    nib.save(nib.Nifti1Image(atlas, affine=np.eye(4)), str(template_atlas))
    monkeypatch.setattr(fc_module, "_known_template_atlas_roots", lambda: [template_root])

    result = run_python_functional_connectivity_subject(
        subject_id=sid,
        derivatives_dir=str(d),
        atlas_path=str(template_atlas),
    )

    assert result["ok"] is True, result["errors"]
    assert result["stage_status"] == "succeeded"
    assert result["fc_status"] == "atlas_grounded_computed"
    assert result["atlas_grounded"] is True
    assert result["preview_only"] is False
    atlas_file = Path(result["atlas_file"])
    assert atlas_file.exists()
    assert str(atlas_file).startswith(str(d))
    assert "registered_templates" in str(atlas_file)
    assert atlas_file != template_atlas
    provenance = json.loads(Path(result["provenance_json"]).read_text(encoding="utf-8"))
    assert provenance["atlas_source"] == "registered_template_atlas"
    assert provenance["atlas_template_source"]["source_checksum"]
    assert Path(provenance["atlas_template_source"]["provenance_path"]).exists()


def test_python_fc_rejects_unregistered_external_atlas(tmp_path: Path):
    d = tmp_path / "derivatives"
    sid = "sub-001"
    fd = d / "rsfmri_preproc" / sid / "func"
    fd.mkdir(parents=True)
    ip = fd / "filt_resid_rsub-001_bold.nii"
    nib.save(nib.Nifti1Image(np.zeros((4, 4, 3, 8), dtype=np.float32), np.eye(4)), str(ip))
    atlas_path = tmp_path / "outside" / "atlas.nii"
    atlas_path.parent.mkdir()
    nib.save(nib.Nifti1Image(np.ones((4, 4, 3), dtype=np.int16), np.eye(4)), str(atlas_path))

    result = run_python_functional_connectivity_subject(
        subject_id=sid,
        derivatives_dir=str(d),
        atlas_path=str(atlas_path),
    )

    assert result["ok"] is False
    assert result["stage_status"] == "failed"
    assert "unsafe atlas" in " ".join(result["errors"]).lower()


def test_python_fc_rejects_atlas_shape_mismatch(tmp_path: Path):
    d = tmp_path / "derivatives"
    sid = "sub-001"
    fd = d / "rsfmri_preproc" / sid / "func"
    fd.mkdir(parents=True)
    ip = fd / "filt_resid_rsub-001_bold.nii"
    nib.save(nib.Nifti1Image(np.zeros((4, 4, 3, 8), dtype=np.float32), np.eye(4)), str(ip))
    atlas_path = d / "atlases" / "bad_atlas.nii"
    atlas_path.parent.mkdir(parents=True)
    nib.save(nib.Nifti1Image(np.ones((3, 4, 3), dtype=np.int16), np.eye(4)), str(atlas_path))

    result = run_python_functional_connectivity_subject(
        subject_id=sid,
        derivatives_dir=str(d),
        atlas_path=str(atlas_path),
    )

    assert result["ok"] is False
    assert result["stage_status"] == "failed"
    assert "shape" in " ".join(result["errors"]).lower()
