"""Deterministic geometry and rigid-registration helpers for ACPC alignment.

The functions in this module intentionally have no file-system or environment
dependencies.  They operate in RAS+ world millimetres and are shared by the
native ACPC stage and its tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin

import numpy as np

from src.backend.app.native_preproc.core.resampling import resample_spatial_to_reference


class AcpcGeometryError(ValueError):
    """Raised when an image or landmark set cannot define an ACPC frame."""


@dataclass(frozen=True)
class RigidRegistrationResult:
    """A subject-world to template-world rigid transform and its QC metrics."""

    subject_to_template: np.ndarray
    converged: bool
    nmi_before: float
    nmi_after: float
    iterations: int


def validate_3d_image(data: np.ndarray, affine: np.ndarray, *, name: str) -> None:
    volume = np.asarray(data)
    matrix = np.asarray(affine, dtype=np.float64)
    if volume.ndim != 3:
        raise AcpcGeometryError(f"{name} must be 3D, got shape {volume.shape}.")
    if not np.isfinite(volume).any() or not np.any(np.isfinite(volume) & (volume != 0)):
        raise AcpcGeometryError(f"{name} has no finite non-zero voxels.")
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        raise AcpcGeometryError(f"{name} affine must be a finite 4x4 matrix.")
    determinant = float(np.linalg.det(matrix[:3, :3]))
    if not np.isfinite(determinant) or abs(determinant) < 1e-8:
        raise AcpcGeometryError(f"{name} affine is not invertible.")


def rigid_matrix(parameters: np.ndarray) -> np.ndarray:
    """Return a float64 4x4 transform from xyz rotations (rad) and mm shift."""

    values = np.asarray(parameters, dtype=np.float64)
    if values.shape != (6,) or not np.all(np.isfinite(values)):
        raise AcpcGeometryError("Rigid parameters must be six finite values.")
    rx, ry, rz, tx, ty, tz = values
    rotation_x = np.array(((1, 0, 0), (0, cos(rx), -sin(rx)), (0, sin(rx), cos(rx))), dtype=np.float64)
    rotation_y = np.array(((cos(ry), 0, sin(ry)), (0, 1, 0), (-sin(ry), 0, cos(ry))), dtype=np.float64)
    rotation_z = np.array(((cos(rz), -sin(rz), 0), (sin(rz), cos(rz), 0), (0, 0, 1)), dtype=np.float64)
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = rotation_z @ rotation_y @ rotation_x
    result[:3, 3] = (tx, ty, tz)
    return result


def is_right_handed_rigid(matrix: np.ndarray, *, tolerance: float = 1e-5) -> bool:
    candidate = np.asarray(matrix, dtype=np.float64)
    if candidate.shape != (4, 4) or not np.all(np.isfinite(candidate)):
        return False
    rotation = candidate[:3, :3]
    return bool(
        np.allclose(rotation.T @ rotation, np.eye(3), atol=tolerance)
        and np.isclose(np.linalg.det(rotation), 1.0, atol=tolerance)
        and np.allclose(candidate[3], (0.0, 0.0, 0.0, 1.0), atol=tolerance)
    )


def normalized_mutual_information(reference: np.ndarray, moving: np.ndarray, *, bins: int = 64) -> float:
    ref = np.asarray(reference, dtype=np.float64).ravel()
    mov = np.asarray(moving, dtype=np.float64).ravel()
    valid = np.isfinite(ref) & np.isfinite(mov)
    if int(valid.sum()) < 128:
        return 0.0
    histogram, _, _ = np.histogram2d(ref[valid], mov[valid], bins=int(bins))
    total = float(histogram.sum())
    if total <= 0:
        return 0.0
    probability = histogram / total

    def entropy(values: np.ndarray) -> float:
        nonzero = values[values > 0]
        return float(-np.sum(nonzero * np.log(nonzero)))

    joint = entropy(probability)
    return 0.0 if joint <= 0 else float((entropy(probability.sum(axis=0)) + entropy(probability.sum(axis=1))) / joint)


def _world_center(data: np.ndarray, affine: np.ndarray) -> np.ndarray:
    from scipy.ndimage import center_of_mass

    values = np.nan_to_num(np.asarray(data, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    # Robustly suppress air/background without assuming a scanner intensity scale.
    positive = values[values > 0]
    if positive.size == 0:
        raise AcpcGeometryError("Image has no positive intensity for rigid registration.")
    threshold = float(np.percentile(positive, 20.0))
    weights = np.where(values >= threshold, values, 0.0)
    center = np.asarray(center_of_mass(weights), dtype=np.float64)
    if center.shape != (3,) or not np.all(np.isfinite(center)):
        raise AcpcGeometryError("Unable to calculate finite image centre of mass.")
    return (np.asarray(affine, dtype=np.float64) @ np.append(center, 1.0))[:3]


def estimate_rigid_subject_to_template(
    subject_data: np.ndarray,
    subject_affine: np.ndarray,
    template_data: np.ndarray,
    template_affine: np.ndarray,
    *,
    max_iterations: int = 80,
) -> RigidRegistrationResult:
    """Estimate a deterministic 6-DOF subject-to-template rigid transform.

    Powell optimisation is deterministic for fixed arrays and deliberately
    bounded.  It starts from centre-of-mass translation and optimises NMI on
    the template grid; no external executable or GPU path is involved.
    """

    validate_3d_image(subject_data, subject_affine, name="subject T1w")
    validate_3d_image(template_data, template_affine, name="ACPC template")
    subject_center = _world_center(subject_data, subject_affine)
    template_center = _world_center(template_data, template_affine)
    start = np.concatenate((np.zeros(3, dtype=np.float64), template_center - subject_center))

    def score(values: np.ndarray) -> float:
        transform = rigid_matrix(values)
        resampled = resample_spatial_to_reference(
            subject_data,
            subject_affine,
            template_data.shape,
            template_affine,
            input_to_reference_affine=transform,
            order=1,
            output_dtype=np.float32,
        )
        return normalized_mutual_information(template_data, resampled)

    before = score(start)
    from scipy.optimize import minimize

    result = minimize(
        lambda values: -score(values),
        start,
        method="Powell",
        bounds=[(-0.61, 0.61), (-0.61, 0.61), (-0.61, 0.61), (-100, 100), (-100, 100), (-100, 100)],
        options={"maxiter": int(max_iterations), "xtol": 1e-3, "ftol": 1e-4},
    )
    optimized = np.asarray(result.x, dtype=np.float64)
    after = score(optimized)
    # A bounded optimiser may report numerical convergence at a poorer local
    # point.  Preserve the deterministic centre-of-mass initial alignment in
    # that case rather than emitting a degraded "successful" transform.
    if after + 1e-6 < before:
        optimized = start
        after = before
    transform = rigid_matrix(optimized)
    return RigidRegistrationResult(
        subject_to_template=transform,
        converged=bool(result.success and is_right_handed_rigid(transform)),
        nmi_before=float(before),
        nmi_after=float(after),
        iterations=int(getattr(result, "nit", 0) or 0),
    )


def transform_point(matrix: np.ndarray, point_mm: np.ndarray) -> np.ndarray:
    transform = np.asarray(matrix, dtype=np.float64)
    point = np.asarray(point_mm, dtype=np.float64)
    if transform.shape != (4, 4) or point.shape != (3,):
        raise AcpcGeometryError("Expected a 4x4 matrix and a three-dimensional point.")
    mapped = transform @ np.append(point, 1.0)
    if not np.all(np.isfinite(mapped)):
        raise AcpcGeometryError("Point transformation produced non-finite coordinates.")
    return mapped[:3]


def construct_acpc_frame(ac_mm: np.ndarray, pc_mm: np.ndarray, msp_normal: np.ndarray) -> np.ndarray:
    """Return an ACPC-to-subject RAS+ frame with AC as its origin.

    +Y is AC minus PC, +X follows the mapped mid-sagittal normal, and +Z is
    chosen to form a right-handed orthonormal frame.
    """

    ac = np.asarray(ac_mm, dtype=np.float64)
    pc = np.asarray(pc_mm, dtype=np.float64)
    normal = np.asarray(msp_normal, dtype=np.float64)
    if ac.shape != (3,) or pc.shape != (3,) or normal.shape != (3,):
        raise AcpcGeometryError("AC, PC, and MSP normal must be 3-vectors.")
    y_axis = ac - pc
    length = float(np.linalg.norm(y_axis))
    if not np.isfinite(length) or length <= 1e-6:
        raise AcpcGeometryError("AC-PC distance must be positive.")
    y_axis /= length
    x_axis = normal - y_axis * float(np.dot(normal, y_axis))
    x_norm = float(np.linalg.norm(x_axis))
    if not np.isfinite(x_norm) or x_norm <= 1e-6:
        raise AcpcGeometryError("MSP normal is degenerate with the AC-PC axis.")
    x_axis /= x_norm
    z_axis = np.cross(x_axis, y_axis)
    z_norm = float(np.linalg.norm(z_axis))
    if not np.isfinite(z_norm) or z_norm <= 1e-6:
        raise AcpcGeometryError("Could not construct a superior ACPC axis.")
    z_axis /= z_norm
    x_axis = np.cross(y_axis, z_axis)
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = np.column_stack((x_axis, y_axis, z_axis))
    result[:3, 3] = ac
    if not is_right_handed_rigid(result):
        raise AcpcGeometryError("Constructed ACPC frame is not right-handed rigid.")
    return result


def point_inside_foreground(data: np.ndarray, affine: np.ndarray, point_mm: np.ndarray) -> bool:
    inverse = np.linalg.inv(np.asarray(affine, dtype=np.float64))
    voxel = (inverse @ np.append(np.asarray(point_mm, dtype=np.float64), 1.0))[:3]
    index = np.rint(voxel).astype(int)
    if np.any(index < 0) or np.any(index >= np.asarray(data.shape)):
        return False
    values = np.asarray(data, dtype=np.float64)
    positive = values[np.isfinite(values) & (values > 0)]
    return bool(positive.size and values[tuple(index)] >= np.percentile(positive, 5.0))
