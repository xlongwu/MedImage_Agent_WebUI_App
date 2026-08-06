"""Template-rigid automatic ACPC estimation and derivative writing.

This is a deterministic alignment stage, not a direct anatomical landmark
detector.  Its landmark fields are deliberately named ``estimated_*``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from src.backend.app.native_preproc.core.acpc import (
    AcpcGeometryError,
    construct_acpc_frame,
    estimate_rigid_subject_to_template,
    is_right_handed_rigid,
    point_inside_foreground,
    transform_point,
    validate_3d_image,
)
from src.backend.app.native_preproc.core.resampling import resample_spatial_to_reference
from src.backend.app.native_preproc.io.derivative_naming import derivative_path, nifti_stem
from src.backend.app.native_preproc.io.nifti_io import _nibabel, load_nifti, save_nifti
from src.backend.app.native_preproc.orchestrator.artifact_registry import build_artifact_ref, file_sha256, write_stage_sidecars
from src.backend.app.native_preproc.stages._common import context_from_output_dir, stage_result
from src.backend.app.schemas.native_preproc import NativePreprocQC
from src.backend.app.runtime.atomic_file import atomic_write_json


ACPC_ALGORITHM_VERSION = "template_rigid_acpc_v1"
DEFAULT_TEMPLATE_ID = "spm12_avg152_t1_ras"


@dataclass(frozen=True)
class AcpcReference:
    template_id: str
    template_path: Path
    template_sha256: str
    coordinate_system: str
    ac_mm: np.ndarray
    pc_mm: np.ndarray
    msp_normal: np.ndarray
    source: dict[str, Any]


def _resource_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "acpc_reference"


def load_acpc_reference(template_id: str = DEFAULT_TEMPLATE_ID) -> AcpcReference:
    """Load a checksum-pinned bundled reference manifest and image."""

    manifest_path = _resource_dir() / f"{template_id}.json"
    if not manifest_path.is_file():
        raise AcpcGeometryError(f"Unknown ACPC template_id: {template_id}.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AcpcGeometryError(f"Invalid ACPC reference manifest: {manifest_path}.") from exc
    if int(manifest.get("schema_version") or 0) != 1:
        raise AcpcGeometryError("Unsupported ACPC reference manifest schema.")
    template_path = manifest_path.parent / str(manifest.get("template_filename") or "")
    if not template_path.is_file():
        raise AcpcGeometryError(f"ACPC template image is missing: {template_path}.")
    actual_checksum = file_sha256(template_path)
    expected_checksum = str(manifest.get("template_sha256") or "").lower()
    if not expected_checksum or actual_checksum.lower() != expected_checksum:
        raise AcpcGeometryError("ACPC template checksum does not match its approved manifest.")
    return AcpcReference(
        template_id=str(manifest["template_id"]),
        template_path=template_path,
        template_sha256=actual_checksum,
        coordinate_system=str(manifest["coordinate_system"]),
        ac_mm=np.asarray(manifest["ac_mm"], dtype=np.float64),
        pc_mm=np.asarray(manifest["pc_mm"], dtype=np.float64),
        msp_normal=np.asarray(manifest["msp_normal"], dtype=np.float64),
        source=dict(manifest.get("source") or {}),
    )


def _load_ras(path: str | Path):
    nib = _nibabel()
    image_path = Path(path)
    canonical = nib.as_closest_canonical(nib.load(str(image_path)))
    return np.asarray(canonical.get_fdata(dtype=np.float32), dtype=np.float32), np.asarray(canonical.affine, dtype=np.float64), canonical.header.copy(), image_path


def run_acpc_alignment(
    source_t1w: str | Path,
    output_dir: str | Path,
    *,
    template_id: str = DEFAULT_TEMPLATE_ID,
    interpolation: str = "linear",
    run_id: str = "native_acpc_run",
    subject_id: str = "",
    session_id: str = "",
    source_artifact_id: str = "",
):
    """Write reloadable ACPC derivatives or fail closed without final artifacts."""

    stage_id = "auto_acpc_align"
    context = context_from_output_dir(output_dir, run_id=run_id, subject_id=subject_id, session_id=session_id)
    parameters: dict[str, Any] = {
        "algorithm": ACPC_ALGORITHM_VERSION,
        "template_id": template_id,
        "interpolation": interpolation,
        "coordinate_semantics": "estimated_template_back_projection_not_manual_landmarks",
    }
    warnings = ["estimated_landmarks_require_independent_manual_reference_validation"]
    errors: list[str] = []
    try:
        if interpolation not in {"linear", "cubic"}:
            raise AcpcGeometryError("interpolation must be 'linear' or 'cubic'.")
        subject_data, subject_affine, subject_header, subject_path = _load_ras(source_t1w)
        reference = load_acpc_reference(template_id)
        template_data, template_affine, _template_header, _template_path = _load_ras(reference.template_path)
        validate_3d_image(subject_data, subject_affine, name="subject T1w")
        validate_3d_image(template_data, template_affine, name="ACPC template")
        registration = estimate_rigid_subject_to_template(
            subject_data,
            subject_affine,
            template_data,
            template_affine,
        )
        template_to_subject = np.linalg.inv(registration.subject_to_template)
        estimated_ac = transform_point(template_to_subject, reference.ac_mm)
        estimated_pc = transform_point(template_to_subject, reference.pc_mm)
        mapped_normal = registration.subject_to_template[:3, :3].T @ reference.msp_normal
        acpc_to_subject = construct_acpc_frame(estimated_ac, estimated_pc, mapped_normal)
        order = 1 if interpolation == "linear" else 3
        aligned = resample_spatial_to_reference(
            subject_data,
            subject_affine,
            template_data.shape,
            template_affine,
            input_to_reference_affine=registration.subject_to_template,
            order=order,
            output_dtype=np.float32,
        )
        output_dir_path = context.stage_artifact_dir(stage_id)
        aligned_path = derivative_path(output_dir_path, subject_path, stage_id=stage_id, suffix="acpc_T1w")
        transform_path = output_dir_path / f"{nifti_stem(subject_path)}_desc-subject_to_acpc_rigid.npy"
        landmarks_path = output_dir_path / f"{nifti_stem(subject_path)}_desc-acpc_landmarks.json"
        save_nifti(aligned_path, aligned, template_affine, header=subject_header, dtype=np.float32)
        np.save(transform_path, registration.subject_to_template.astype(np.float64))
        landmark_payload = {
            "schema_version": 1,
            "coordinate_system": reference.coordinate_system,
            "estimated_ac_mm": [float(value) for value in estimated_ac],
            "estimated_pc_mm": [float(value) for value in estimated_pc],
            "msp_normal": [float(value) for value in mapped_normal / np.linalg.norm(mapped_normal)],
            "landmark_kind": "template_back_projected_estimate",
            "template_id": reference.template_id,
            "template_sha256": reference.template_sha256,
            "source_t1w_sha256": file_sha256(subject_path),
            "subject_to_template_rigid_matrix": registration.subject_to_template.tolist(),
            "acpc_to_subject_matrix": acpc_to_subject.tolist(),
        }
        atomic_write_json(landmarks_path, landmark_payload, schema_version=1)
        checks = {
            "optimizer_converged": registration.converged,
            "nmi_not_degraded": registration.nmi_after + 1e-6 >= registration.nmi_before,
            "nmi_threshold": registration.nmi_after >= 1.0,
            "ac_in_foreground": point_inside_foreground(subject_data, subject_affine, estimated_ac),
            "pc_in_foreground": point_inside_foreground(subject_data, subject_affine, estimated_pc),
            "subject_to_template_rigid": is_right_handed_rigid(registration.subject_to_template),
            "acpc_frame_right_handed": is_right_handed_rigid(acpc_to_subject),
            "output_reloadable": False,
        }
        reloaded = load_nifti(aligned_path)
        checks["output_reloadable"] = bool(
            reloaded.data.shape == aligned.shape and np.all(np.isfinite(reloaded.data)) and np.asarray(reloaded.affine).shape == (4, 4)
        )
        review_required = not all(checks.values())
        qc = NativePreprocQC(
            status="pass" if not review_required else "fail",
            metrics={
                "converged": registration.converged,
                "nmi_before": registration.nmi_before,
                "nmi_after": registration.nmi_after,
                "optimizer_iterations": registration.iterations,
                "checks": checks,
                "review_required": review_required,
                "failure_code": "ACPC_QC_FAILED" if review_required else "",
            },
            thresholds={"minimum_nmi": 1.0, "rigid_transform": True, "foreground_landmarks": True},
            warnings=warnings,
            errors=[] if not review_required else ["ACPC_QC_FAILED"],
        )
        parameters.update({"template_sha256": reference.template_sha256, "source_t1w_sha256": file_sha256(subject_path)})
        if review_required:
            # Remove only this stage's candidate outputs.  No final artifact is
            # registered when QC fails, matching the fail-closed contract.
            for candidate in (aligned_path, transform_path, landmarks_path):
                candidate.unlink(missing_ok=True)
            return stage_result(context, stage_id=stage_id, parameters=parameters, status="failed", capability_level="computed", qc=qc, warnings=warnings, errors=["ACPC_QC_FAILED"])
        input_artifacts = [
            build_artifact_ref(
                subject_path,
                artifact_type="t1w",
                artifact_id=source_artifact_id or None,
                metadata={"role": "registered_source_t1w", "read_only": True},
            )
        ]
        output_artifacts = [
            build_artifact_ref(aligned_path, artifact_type="acpc_t1w", metadata={"space": "ACPC", "template_id": reference.template_id}),
            build_artifact_ref(transform_path, artifact_type="transform_matrix", metadata={"maps": "subject_ras_to_template_acpc_ras", "matrix_dtype": "float64"}),
            build_artifact_ref(landmarks_path, artifact_type="acpc_landmarks", metadata={"coordinate_system": reference.coordinate_system, "estimated": True}),
        ]
        result = stage_result(
            context,
            stage_id=stage_id,
            parameters=parameters,
            status="succeeded",
            capability_level="computed",
            qc=qc,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
            warnings=warnings,
        )
        qc_artifact = build_artifact_ref(
            context.qc_path(stage_id),
            artifact_type="qc_json",
            metadata={"stage_id": stage_id, "review_required": False},
        )
        result = result.model_copy(update={"output_artifacts": [*result.output_artifacts, qc_artifact]})
        write_stage_sidecars(context, result)
        return result
    except Exception as exc:
        errors.append(str(exc))
        qc = NativePreprocQC(status="fail", metrics={"review_required": True, "failure_code": "ACPC_ALIGNMENT_FAILED"}, warnings=warnings, errors=errors)
        return stage_result(context, stage_id=stage_id, parameters=parameters, status="failed", capability_level="computed", qc=qc, warnings=warnings, errors=errors)


__all__ = ["ACPC_ALGORITHM_VERSION", "DEFAULT_TEMPLATE_ID", "AcpcReference", "load_acpc_reference", "run_acpc_alignment"]
