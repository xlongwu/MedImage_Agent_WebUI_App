from pathlib import Path

import nibabel as nib
import numpy as np

from desktop.packaging.create_agent_first_e2e_fixture import create_atlas, create_template


def test_create_atlas_matches_bold_grid_and_has_two_labels(tmp_path: Path) -> None:
    bold = tmp_path / "bold.nii.gz"
    atlas = tmp_path / "atlas.nii.gz"
    nib.save(
        nib.Nifti1Image(np.zeros((6, 4, 3, 5), dtype=np.float32), np.eye(4)),
        str(bold),
    )

    create_atlas(bold_path=bold, output_path=atlas)

    image = nib.load(str(atlas))
    assert image.shape == (6, 4, 3)
    assert set(np.unique(np.asarray(image.dataobj)).tolist()) == {1, 2}


def test_create_template_uses_bold_geometry_and_temporal_mean(tmp_path: Path) -> None:
    bold = tmp_path / "bold.nii.gz"
    template = tmp_path / "template.nii.gz"
    data = np.stack(
        [np.full((6, 4, 3), value, dtype=np.float32) for value in range(1, 6)], axis=3
    )
    nib.save(nib.Nifti1Image(data, np.eye(4)), str(bold))

    create_template(bold_path=bold, output_path=template)

    image = nib.load(str(template))
    assert image.shape == (6, 4, 3)
    assert np.allclose(np.asarray(image.dataobj), 3.0)
