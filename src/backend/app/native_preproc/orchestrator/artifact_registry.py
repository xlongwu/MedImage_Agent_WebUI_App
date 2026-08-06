"""Artifact registration helpers for native preprocessing stages."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.backend.app.native_preproc.io.nifti_io import nifti_summary
from src.backend.app.native_preproc.orchestrator.state import NativePreprocRunContext
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.schemas.native_preproc import (
    NativePreprocArtifactRef,
    NativePreprocArtifactType,
    NativePreprocStageResult,
)

_NIFTI_ARTIFACTS = {
    "acpc_t1w",
    "bold_4d",
    "t1w",
    "mean_functional",
    "brain_mask",
    "gm_map",
    "wm_map",
    "csf_map",
    "normalized_bold",
    "smoothed_bold",
    "residual_bold",
    "detrended_bold",
    "filtered_bold",
    "alff_map",
    "falff_map",
    "reho_map",
    "atlas",
    "atlas_resampled",
}


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_artifact_ref(
    path: str | Path,
    *,
    artifact_type: NativePreprocArtifactType,
    artifact_id: str | None = None,
    source_artifact_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> NativePreprocArtifactRef:
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise FileNotFoundError(f"Artifact does not exist: {artifact_path}")
    merged_metadata = dict(metadata or {})
    shape: list[int] = []
    dtype = ""
    if artifact_type in _NIFTI_ARTIFACTS:
        summary = nifti_summary(artifact_path)
        shape = summary["shape"]
        dtype = summary["dtype"]
        merged_metadata.update({key: value for key, value in summary.items() if key not in {"shape", "dtype"}})
    elif artifact_path.suffix == ".npy":
        try:
            import numpy as np

            array = np.load(artifact_path)
            shape = [int(value) for value in array.shape]
            dtype = str(array.dtype)
        except Exception as exc:
            merged_metadata["array_summary_error"] = str(exc)
    return NativePreprocArtifactRef(
        artifact_id=artifact_id or artifact_path.stem,
        artifact_type=artifact_type,
        path=str(artifact_path),
        path_kind="absolute_local" if artifact_path.is_absolute() else "project_relative",
        shape=shape,
        dtype=dtype,
        checksum=file_sha256(artifact_path),
        source_artifact_ids=source_artifact_ids or [],
        metadata=merged_metadata,
    )


def write_stage_sidecars(
    context: NativePreprocRunContext,
    result: NativePreprocStageResult,
) -> dict[str, Path]:
    """Write stage manifest, provenance, and QC JSON sidecars atomically."""

    context.ensure_directories()
    provenance_path = context.provenance_path(result.stage_id)
    qc_path = context.qc_path(result.stage_id)
    manifest_path = context.manifest_path(result.stage_id)

    atomic_write_json(
        provenance_path,
        result.provenance.model_dump(mode="json"),
        schema_version=1,
    )
    atomic_write_json(
        qc_path,
        result.qc.model_dump(mode="json"),
        schema_version=1,
    )
    atomic_write_json(
        manifest_path,
        {
            "run_id": context.run_id,
            "stage_id": result.stage_id,
            "status": result.status,
            "capability_level": result.capability_level,
            "validation_status": result.validation_status,
            "input_artifacts": [artifact.model_dump(mode="json") for artifact in result.input_artifacts],
            "output_artifacts": [artifact.model_dump(mode="json") for artifact in result.output_artifacts],
            "parameters": result.parameters,
            "warnings": result.warnings,
            "errors": result.errors,
            "provenance_path": str(provenance_path),
            "qc_path": str(qc_path),
        },
        schema_version=1,
    )
    return {
        "manifest": manifest_path,
        "provenance": provenance_path,
        "qc_json": qc_path,
    }
