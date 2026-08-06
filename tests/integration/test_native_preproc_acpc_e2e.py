from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from src.backend.app.native_preproc.io.nifti_io import load_nifti
from src.backend.app.native_preproc.orchestrator.artifact_registry import file_sha256
from src.backend.app.native_preproc.stages.acpc_alignment import load_acpc_reference
from src.backend.app.schemas.native_preproc_api import AcpcRequest
from src.backend.app.services.native_acpc import execute_acpc_request
from src.backend.app.services.preprocessing_artifact_registry import REGISTRY_FILENAME, load_artifact_registry


def test_registered_t1_to_acpc_artifacts_preserves_rawdata(tmp_path: Path) -> None:
    source = tmp_path / "rawdata" / "sub-001" / "anat" / "sub-001_T1w.nii"
    source.parent.mkdir(parents=True)
    shutil.copyfile(load_acpc_reference().template_path, source)
    source_checksum = file_sha256(source)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / REGISTRY_FILENAME).write_text(
        json.dumps(
            {
                "registry_schema_version": "1",
                "artifacts": [
                    {
                        "artifact_id": "source-t1",
                        "artifact_type": "converted_t1w",
                        "path": "rawdata/sub-001/anat/sub-001_T1w.nii",
                        "path_kind": "project_relative",
                        "subject_id": "sub-001",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = execute_acpc_request(
        AcpcRequest(
            project_id="acpc-e2e",
            project_dir=str(tmp_path),
            source_t1_artifact_id="source-t1",
            output_root=str(tmp_path / "derivatives"),
        ),
        run_id="acpc-e2e-run",
    )

    assert result.ok is True
    assert result.status == "computed"
    assert result.qc.review_required is False
    assert result.landmarks is not None
    assert result.landmarks.coordinate_system == "RAS+ mm"
    assert file_sha256(source) == source_checksum
    registry = load_artifact_registry(result.registry_path)
    records = {record["artifact_type"]: record for record in registry["artifacts"]}
    assert set(records) == {"acpc_t1w", "transform_matrix", "acpc_landmarks", "qc_json"}
    assert all(record["source_artifact_ids"] == ["source-t1"] for record in records.values())
    aligned = tmp_path / records["acpc_t1w"]["path"]
    transform = tmp_path / records["transform_matrix"]["path"]
    landmarks = tmp_path / records["acpc_landmarks"]["path"]
    assert load_nifti(aligned).data.ndim == 3
    assert np.load(transform).shape == (4, 4)
    landmark_payload = json.loads(landmarks.read_text(encoding="utf-8"))
    assert landmark_payload["landmark_kind"] == "template_back_projected_estimate"
