"""Create licensed synthetic resources for isolated Agent-first desktop E2E tests."""

from __future__ import annotations

import argparse
from tempfile import TemporaryDirectory
from pathlib import Path
import sys

import nibabel as nib
import numpy as np


def create_atlas(*, bold_path: Path, output_path: Path) -> Path:
    image = nib.load(str(bold_path))
    if len(image.shape) != 4 or min(image.shape[:3]) < 2:
        raise ValueError("The BIDS smoke BOLD fixture must be a non-empty 4D image.")
    labels = np.zeros(image.shape[:3], dtype=np.int16)
    midpoint = labels.shape[0] // 2
    labels[:midpoint, :, :] = 1
    labels[midpoint:, :, :] = 2
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(labels, image.affine, image.header), str(output_path))
    return output_path


def create_template(*, bold_path: Path, output_path: Path) -> Path:
    """Create a project-owned 3D template with the fixture's actual geometry."""

    image = nib.load(str(bold_path))
    if len(image.shape) != 4 or min(image.shape[:3]) < 2:
        raise ValueError("The BIDS smoke BOLD fixture must be a non-empty 4D image.")
    template = np.asarray(image.dataobj, dtype=np.float32).mean(axis=3)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(template, image.affine), str(output_path))
    return output_path


def create_resources_from_dicom(
    *, dicom_dir: Path, atlas_path: Path, template_path: Path
) -> tuple[Path, Path]:
    """Derive licensed smoke resources from the converter's actual output geometry."""

    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    from src.backend.app.native_preproc.io.dicom_to_nifti import convert_dicom_series

    with TemporaryDirectory(prefix="medimage-agent-dicom-atlas-") as temporary_dir:
        preview = Path(temporary_dir) / "preview_bold.nii.gz"
        convert_dicom_series(dicom_dir, preview, subject_id="sub-001", modality="func")
        return (
            create_atlas(bold_path=preview, output_path=atlas_path),
            create_template(bold_path=preview, output_path=template_path),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bold", type=Path)
    source.add_argument("--dicom-dir", type=Path)
    parser.add_argument("--atlas", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    args = parser.parse_args()
    if args.bold is not None:
        create_atlas(bold_path=args.bold.resolve(), output_path=args.atlas.resolve())
        create_template(bold_path=args.bold.resolve(), output_path=args.template.resolve())
    else:
        create_resources_from_dicom(
            dicom_dir=args.dicom_dir.resolve(),
            atlas_path=args.atlas.resolve(),
            template_path=args.template.resolve(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
