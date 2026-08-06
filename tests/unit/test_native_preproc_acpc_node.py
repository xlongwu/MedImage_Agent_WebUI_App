from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.backend.app.native_preproc.orchestrator.artifact_registry import file_sha256
from src.backend.app.native_preproc.stages.acpc_alignment import load_acpc_reference
from src.backend.app.schemas.native_preproc_api import AcpcRequest
from src.backend.app.services.native_acpc import execute_acpc_request
from src.backend.app.services.preprocessing_artifact_registry import REGISTRY_FILENAME, load_artifact_registry


def test_acpc_request_reads_registered_t1_and_registers_derivatives(tmp_path: Path) -> None:
    source = tmp_path / "rawdata" / "sub-001" / "anat" / "sub-001_T1w.nii"
    source.parent.mkdir(parents=True)
    shutil.copyfile(load_acpc_reference().template_path, source)
    source_checksum = file_sha256(source)
    registry_path = tmp_path / "data" / REGISTRY_FILENAME
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "registry_schema_version": "1",
                "artifacts": [
                    {
                        "artifact_id": "registered-t1",
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
            project_id="project-1",
            project_dir=str(tmp_path),
            source_t1_artifact_id="registered-t1",
            output_root=str(tmp_path / "derivatives"),
        ),
        run_id="acpc-run-1",
    )
    assert result.ok and result.status == "computed"
    assert result.landmarks is not None
    assert result.landmarks.estimated_ac_mm == [0.0, 0.0, 0.0]
    assert file_sha256(source) == source_checksum
    registry = load_artifact_registry(result.registry_path)
    artifacts = registry["artifacts"]
    assert {item["artifact_type"] for item in artifacts} == {"acpc_t1w", "transform_matrix", "acpc_landmarks", "qc_json"}
    assert all(item["source_artifact_ids"] == ["registered-t1"] for item in artifacts)


def test_acpc_request_rejects_rawdata_output_root(tmp_path: Path) -> None:
    result = execute_acpc_request(
        AcpcRequest(
            project_id="project-1",
            project_dir=str(tmp_path),
            source_t1_artifact_id="missing",
            output_root=str(tmp_path / "rawdata"),
        ),
        run_id="acpc-run-1",
    )
    assert result.status == "blocked"
    assert "derivatives" in result.errors[0]
