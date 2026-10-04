"""Read-only validation helpers for native preprocessing run artifacts."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

Check = dict[str, Any]

_SUBJECT_RE = re.compile(r"sub-[A-Za-z0-9]+")
_NIFTI_SUFFIXES = (".nii", ".nii.gz")
_BOLD_TYPES = {
    "bold_4d",
    "residual_bold",
    "detrended_bold",
    "filtered_bold",
    "normalized_bold",
    "smoothed_bold",
}


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "_schema_version" in payload and "data" in payload:
        data = payload["data"]
        return data if isinstance(data, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _check(
    checks: list[Check],
    name: str,
    passed: bool,
    *,
    severity: str = "error",
    detail: str = "",
    metrics: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "severity": severity,
            "detail": detail,
            "metrics": metrics or {},
        }
    )


def _safe_float(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else number


def _array_metrics(array: np.ndarray) -> dict[str, Any]:
    finite = np.isfinite(array)
    finite_count = int(finite.sum())
    total = int(array.size)
    finite_values = array[finite]
    nonzero_fraction = float(np.count_nonzero(finite_values) / finite_count) if finite_count else 0.0
    metrics: dict[str, Any] = {
        "shape": [int(value) for value in array.shape],
        "dtype": str(array.dtype),
        "total_values": total,
        "finite_values": finite_count,
        "nan_count": int(np.isnan(array).sum()) if np.issubdtype(array.dtype, np.floating) else 0,
        "inf_count": int(np.isinf(array).sum()) if np.issubdtype(array.dtype, np.floating) else 0,
        "finite_fraction": float(finite_count / total) if total else 0.0,
        "nonzero_fraction": nonzero_fraction,
    }
    if finite_count:
        metrics.update(
            {
                "min": float(np.min(finite_values)),
                "max": float(np.max(finite_values)),
                "mean": float(np.mean(finite_values)),
                "std": float(np.std(finite_values)),
            }
        )
    if array.ndim == 4 and finite_count:
        temporal_std = np.std(array, axis=-1)
        temporal_std = temporal_std[np.isfinite(temporal_std)]
        metrics["temporal_std_mean"] = float(np.mean(temporal_std)) if temporal_std.size else 0.0
    return metrics


def _validate_nifti(path: Path, artifact_type: str, checks: list[Check]) -> None:
    try:
        import nibabel as nib

        image = nib.load(str(path))
        data = np.asarray(image.dataobj)
    except Exception as exc:
        _check(checks, f"numeric:{artifact_type}:{path.name}", False, detail=f"Failed to load NIfTI: {exc}")
        return

    metrics = _array_metrics(data)
    finite_ok = metrics["finite_fraction"] == 1.0
    nonzero_ok = metrics["nonzero_fraction"] > 0.0
    ndim_ok = data.ndim >= 3
    temporal_ok = True
    if artifact_type in _BOLD_TYPES and data.ndim == 4:
        temporal_ok = float(metrics.get("temporal_std_mean") or 0.0) > 1e-8
    _check(
        checks,
        f"numeric:{artifact_type}:{path.name}",
        finite_ok and nonzero_ok and ndim_ok and temporal_ok,
        detail="NIfTI must be finite, non-empty, non-zero, and temporally varying for 4D BOLD artifacts.",
        metrics=metrics,
    )


def _validate_npy(path: Path, artifact_type: str, checks: list[Check]) -> None:
    try:
        array = np.load(path)
    except Exception as exc:
        _check(checks, f"numeric:{artifact_type}:{path.name}", False, detail=f"Failed to load NPY: {exc}")
        return

    metrics = _array_metrics(array)
    if artifact_type == "transform_matrix":
        affine_shape = array.ndim in {2, 3} and tuple(array.shape[-2:]) == (4, 4)
        metrics.update({"affine_shape": affine_shape})
        _check(
            checks,
            f"numeric:{artifact_type}:{path.name}",
            metrics["finite_fraction"] == 1.0 and metrics["nonzero_fraction"] > 0.0 and affine_shape,
            detail="Transform matrix artifact must be finite, non-zero, and shaped as 4x4 affine matrix/matrices.",
            metrics=metrics,
        )
        return

    square = array.ndim == 2 and array.shape[0] == array.shape[1]
    symmetric = bool(square and np.allclose(array, array.T, atol=1e-5, equal_nan=False))
    diagonal_ok = True
    if artifact_type == "fc_matrix" and square:
        diagonal_ok = bool(np.allclose(np.diag(array), 1.0, atol=1e-5, equal_nan=False))
    metrics.update({"square": square, "symmetric": symmetric, "diagonal_ok": diagonal_ok})
    _check(
        checks,
        f"numeric:{artifact_type}:{path.name}",
        metrics["finite_fraction"] == 1.0
        and metrics["nonzero_fraction"] > 0.0
        and square
        and symmetric
        and diagonal_ok,
        detail="Matrix artifact must be finite, non-zero, square, symmetric, and have a unit diagonal for raw FC.",
        metrics=metrics,
    )


def _validate_table(path: Path, artifact_type: str, checks: list[Check]) -> None:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    numeric_columns: dict[str, list[float]] = {}
    rows = 0
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            for row in reader:
                rows += 1
                for key, value in row.items():
                    number = _safe_float(str(value).strip())
                    if number is not None:
                        numeric_columns.setdefault(key or "", []).append(number)
    except Exception as exc:
        _check(checks, f"numeric:{artifact_type}:{path.name}", False, detail=f"Failed to parse table: {exc}")
        return

    finite_total = 0
    finite_count = 0
    variable_columns = 0
    for values in numeric_columns.values():
        finite_total += len(values)
        finite = [value for value in values if math.isfinite(value)]
        finite_count += len(finite)
        if len(finite) > 1 and float(np.std(finite)) > 1e-12:
            variable_columns += 1
    metrics = {
        "rows": rows,
        "numeric_columns": len(numeric_columns),
        "numeric_values": finite_total,
        "finite_numeric_values": finite_count,
        "variable_numeric_columns": variable_columns,
    }
    _check(
        checks,
        f"numeric:{artifact_type}:{path.name}",
        rows > 0 and finite_total > 0 and finite_count == finite_total,
        detail="Table artifact must contain rows and finite numeric values.",
        metrics=metrics,
    )


def _iter_artifact_refs(payload: Any):
    if isinstance(payload, dict):
        if "artifact_type" in payload and "path" in payload:
            yield payload
        for value in payload.values():
            yield from _iter_artifact_refs(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_artifact_refs(item)


def _artifact_path(ref: dict[str, Any] | None) -> Path | None:
    if not isinstance(ref, dict):
        return None
    value = str(ref.get("path") or "").strip()
    return Path(value) if value else None


def _find_artifact(payloads: tuple[dict[str, Any], ...], artifact_type: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for ref in _iter_artifact_refs(payload):
            if str(ref.get("artifact_type") or "") != artifact_type:
                continue
            key = str(ref.get("path") or "") + "|" + artifact_type
            if key in seen:
                continue
            seen.add(key)
            candidates.append(ref)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            bool((_artifact_path(item) or Path()).exists()),
            bool(item.get("metadata")),
        ),
        reverse=True,
    )
    return candidates[0]


def _read_roi_timeseries_matrix(path: Path) -> tuple[list[str], np.ndarray]:
    from src.backend.app.native_preproc.stages.functional_connectivity import (
        read_roi_timeseries_tsv,
    )

    labels, matrix = read_roi_timeseries_tsv(path)
    return labels, np.asarray(matrix, dtype=np.float32)


def _shape_from_ref(ref: dict[str, Any] | None) -> list[int]:
    if not isinstance(ref, dict):
        return []
    shape = ref.get("shape")
    if isinstance(shape, list):
        try:
            return [int(value) for value in shape]
        except (TypeError, ValueError):
            return []
    return []


def _metadata_from_ref(ref: dict[str, Any] | None) -> dict[str, Any]:
    metadata = ref.get("metadata") if isinstance(ref, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _expected_timepoints(*refs: dict[str, Any] | None) -> int | None:
    for ref in refs:
        shape = _shape_from_ref(ref)
        if len(shape) == 4 and shape[3] > 0:
            return int(shape[3])
        metadata = _metadata_from_ref(ref)
        value = metadata.get("timepoints")
        if value is not None:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
    return None


def _label_count_from_roi_labels(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    payload = _read_json(path)
    labels = payload.get("labels")
    if isinstance(labels, list):
        return len(labels)
    return None


def _positive_label_count_from_ref(ref: dict[str, Any] | None) -> int | None:
    metadata = _metadata_from_ref(ref)
    labels = metadata.get("output_labels") or metadata.get("input_labels")
    if isinstance(labels, list):
        positive: set[int] = set()
        for value in labels:
            try:
                label = int(float(value))
            except (TypeError, ValueError):
                continue
            if label > 0:
                positive.add(label)
        return len(positive)

    path = _artifact_path(ref)
    if path is None or not path.exists() or not path.name.lower().endswith(_NIFTI_SUFFIXES):
        return None
    try:
        import nibabel as nib

        data = np.asarray(nib.load(str(path)).dataobj)
    except Exception:
        return None
    positive_values = {int(value) for value in np.unique(data) if int(value) > 0}
    return len(positive_values)


def _validate_fc_numerical_contract(
    validation_payload: dict[str, Any],
    final_payload: dict[str, Any],
    checks: list[Check],
    *,
    require_fc_numerics: bool,
) -> None:
    payloads = (validation_payload, final_payload)
    bold_ref = _find_artifact(payloads, "filtered_bold")
    atlas_ref = _find_artifact(payloads, "atlas_resampled")
    roi_ref = _find_artifact(payloads, "roi_timeseries")
    roi_labels_ref = _find_artifact(payloads, "roi_labels")
    fc_ref = _find_artifact(payloads, "fc_matrix")

    refs = {
        "filtered_bold": bold_ref,
        "atlas_resampled": atlas_ref,
        "roi_timeseries": roi_ref,
        "roi_labels": roi_labels_ref,
        "fc_matrix": fc_ref,
    }
    present = {
        name: bool((path := _artifact_path(ref)) is not None and path.exists())
        for name, ref in refs.items()
    }
    any_present = any(present.values())
    if not any_present and not require_fc_numerics:
        return

    missing = [name for name, is_present in present.items() if not is_present]
    _check(
        checks,
        "fc_artifacts_present",
        not missing,
        detail="FC numerical validation requires filtered BOLD, resampled atlas, ROI time-series, ROI labels, and FC matrix artifacts.",
        metrics={"missing": missing, "present": present},
    )
    if missing:
        return

    roi_path = _artifact_path(roi_ref)
    fc_path = _artifact_path(fc_ref)
    assert roi_path is not None
    assert fc_path is not None
    try:
        roi_labels, roi_matrix = _read_roi_timeseries_matrix(roi_path)
    except Exception as exc:
        _check(checks, "roi_timeseries_reloadable", False, detail=str(exc))
        return
    try:
        fc_matrix = np.load(fc_path)
    except Exception as exc:
        _check(checks, "fc_matrix_reloadable", False, detail=str(exc))
        return

    roi_finite = bool(np.isfinite(roi_matrix).all())
    fc_finite = bool(np.isfinite(fc_matrix).all())
    _check(
        checks,
        "roi_timeseries_finite",
        roi_finite,
        detail="ROI time-series TSV must not contain NaN or infinite numeric values.",
        metrics={"shape": [int(value) for value in roi_matrix.shape]},
    )
    _check(
        checks,
        "fc_matrix_finite",
        fc_finite,
        detail="Pearson FC NPY must not contain NaN or infinite values.",
        metrics={"shape": [int(value) for value in fc_matrix.shape]},
    )

    expected_timepoints = _expected_timepoints(bold_ref, roi_ref, fc_ref)
    _check(
        checks,
        "roi_timeseries_rows_match_timepoints",
        expected_timepoints is not None and int(roi_matrix.shape[0]) == expected_timepoints,
        detail="ROI time-series rows must equal source BOLD timepoints.",
        metrics={"roi_rows": int(roi_matrix.shape[0]), "expected_timepoints": expected_timepoints},
    )

    square = fc_matrix.ndim == 2 and fc_matrix.shape[0] == fc_matrix.shape[1]
    symmetric = bool(square and np.allclose(fc_matrix, fc_matrix.T, atol=1e-5, equal_nan=False))
    diagonal_unit = bool(square and np.allclose(np.diag(fc_matrix), 1.0, atol=1e-5, equal_nan=False))
    if fc_finite and fc_matrix.size:
        fc_min = float(np.min(fc_matrix))
        fc_max = float(np.max(fc_matrix))
    else:
        fc_min = float("nan")
        fc_max = float("nan")
    in_range = bool(fc_finite and fc_min >= -1.000001 and fc_max <= 1.000001)
    _check(
        checks,
        "fc_matrix_square",
        square,
        detail="Pearson FC matrix must be square.",
        metrics={"shape": [int(value) for value in fc_matrix.shape]},
    )
    _check(
        checks,
        "fc_matrix_symmetric",
        symmetric,
        detail="Pearson FC matrix must be symmetric within atol=1e-5.",
        metrics={"atol": 1e-5},
    )
    _check(
        checks,
        "fc_matrix_diagonal_unit",
        diagonal_unit,
        detail="Pearson FC matrix diagonal must be close to 1 within atol=1e-5.",
        metrics={"atol": 1e-5},
    )
    _check(
        checks,
        "fc_matrix_range",
        in_range,
        detail="Pearson FC values must stay in [-1, 1] within 1e-6 tolerance.",
        metrics={"min": fc_min, "max": fc_max, "tolerance": 1e-6},
    )

    roi_count = int(roi_matrix.shape[1]) if roi_matrix.ndim == 2 else 0
    fc_dim = int(fc_matrix.shape[0]) if square else None
    roi_label_file_count = _label_count_from_roi_labels(_artifact_path(roi_labels_ref))
    atlas_label_count = _positive_label_count_from_ref(atlas_ref)
    _check(
        checks,
        "roi_count_matches_fc_dimension",
        square and roi_count == fc_dim,
        detail="ROI time-series columns must equal FC matrix dimensions.",
        metrics={"roi_count": roi_count, "fc_dim": fc_dim},
    )
    _check(
        checks,
        "roi_count_matches_roi_labels",
        roi_label_file_count is not None and roi_count == roi_label_file_count and roi_count == len(roi_labels),
        detail="ROI time-series columns must equal the generated ROI label JSON count.",
        metrics={
            "roi_count": roi_count,
            "roi_labels_header_count": len(roi_labels),
            "roi_label_file_count": roi_label_file_count,
        },
    )
    _check(
        checks,
        "roi_count_matches_atlas_labels",
        atlas_label_count is not None and roi_count == atlas_label_count,
        detail="ROI count must equal the positive label count in the resampled atlas.",
        metrics={"roi_count": roi_count, "atlas_positive_label_count": atlas_label_count},
    )


def _validate_artifact_payload(validation_payload: dict[str, Any], checks: list[Check]) -> None:
    artifact_section = validation_payload.get("artifact_validation")
    artifacts = artifact_section.get("artifacts") if isinstance(artifact_section, dict) else []
    if not isinstance(artifacts, list):
        _check(checks, "artifact_validation_payload", False, detail="artifact_validation.artifacts is not a list.")
        return
    _check(
        checks,
        "artifact_validation_report",
        validation_payload.get("overall_status") == "pass"
        and artifact_section.get("failed_count") == 0
        and validation_payload.get("summary", {}).get("artifact_failed_count") == 0,
        detail="Native validation report must pass with zero artifact failures.",
        metrics={
            "overall_status": validation_payload.get("overall_status"),
            "artifact_count": len(artifacts),
            "failed_count": artifact_section.get("failed_count"),
        },
    )
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or ""))
        artifact_type = str(item.get("artifact_type") or "")
        if not path.exists():
            _check(checks, f"artifact_exists:{artifact_type}", False, detail=str(path))
            continue
        name = path.name.lower()
        if name.endswith(_NIFTI_SUFFIXES):
            _validate_nifti(path, artifact_type, checks)
        elif path.suffix.lower() == ".npy":
            _validate_npy(path, artifact_type, checks)
        elif path.suffix.lower() in {".tsv", ".csv"}:
            _validate_table(path, artifact_type, checks)


def _subject_candidates_from_payloads(*payloads: dict[str, Any]) -> list[str]:
    text = json.dumps(payloads, ensure_ascii=False)
    return sorted(set(_SUBJECT_RE.findall(text)))


def _validate_subjects(
    group_summary: dict[str, Any],
    validation_payload: dict[str, Any],
    final_payload: dict[str, Any],
    checks: list[Check],
    *,
    require_subject_ids: bool,
) -> None:
    subject_summaries = group_summary.get("subject_summaries")
    if not isinstance(subject_summaries, list):
        subject_summaries = []
    subject_ids = [
        str(item.get("subject_id") or "").strip()
        for item in subject_summaries
        if isinstance(item, dict)
    ]
    unknown_ids = [sid for sid in subject_ids if not sid or sid.lower() == "unknown"]
    candidates = _subject_candidates_from_payloads(validation_payload, final_payload)
    severity = "error" if require_subject_ids else "warning"
    _check(
        checks,
        "subject_summary_counts",
        int(group_summary.get("subject_count") or 0) > 0
        and int(group_summary.get("blocked_subject_count") or 0) == 0,
        detail="Group summary must include subjects and zero blocked subjects.",
        metrics={
            "subject_count": group_summary.get("subject_count"),
            "completed_subject_count": group_summary.get("completed_subject_count"),
            "blocked_subject_count": group_summary.get("blocked_subject_count"),
        },
    )
    _check(
        checks,
        "subject_ids_resolved",
        not unknown_ids,
        severity=severity,
        detail="Subject summaries should contain concrete BIDS subject identifiers.",
        metrics={"subject_ids": subject_ids, "path_subject_candidates": candidates},
    )


def _validate_report_chain(
    project_dir: Path,
    run_dir: Path,
    checks: list[Check],
    *,
    require_report_chain: bool,
    probe_exporter: bool,
) -> None:
    native_summary = run_dir / "artifacts" / "group_summary" / "native_group_summary.json"
    conventional_summary_dir = project_dir / "reports" / "rsfmri" / "group_summary"
    severity = "error" if require_report_chain else "warning"
    conventional_ready = conventional_summary_dir.exists() and any(conventional_summary_dir.glob("*"))
    native_ready = native_summary.exists() and native_summary.stat().st_size > 0
    _check(
        checks,
        "native_group_summary_exists",
        native_ready,
        detail=str(native_summary),
    )
    _check(
        checks,
        "report_exporter_group_summary_source",
        conventional_ready or native_ready,
        severity=severity,
        detail=(
            "Report exporter should be able to use either reports/rsfmri/group_summary or the native "
            f"group summary at {native_summary}."
        ),
        metrics={"conventional_ready": conventional_ready, "native_ready": native_ready},
    )
    if not probe_exporter:
        return
    try:
        from src.backend.app.tools.report_exporter import export_rsfmri_report_package

        work_root = (project_dir / "work").resolve()
        if not work_root.is_relative_to(project_dir) or "rawdata" in {part.casefold() for part in work_root.parts}:
            raise ValueError("REPORT_EXPORT_PROBE_WRITE_ROOT_INVALID")
        if not run_dir.is_relative_to(project_dir):
            raise ValueError("REPORT_EXPORT_PROBE_RUN_OUTSIDE_PROJECT")
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="xp-", dir=work_root) as tmp:
            result = export_rsfmri_report_package(
                project_dir=str(project_dir),
                derivatives_dir=str(project_dir / "derivatives"),
                reports_dir=str(project_dir / "reports"),
                work_dir=str(project_dir / "work"),
                exports_dir=tmp,
                export_id="p",
            )
        warnings = result.get("warnings") if isinstance(result, dict) else []
        _check(
            checks,
            "report_exporter_probe_uses_native_outputs",
            isinstance(result, dict)
            and result.get("exported_subjects_total", 0) > 0
            and result.get("source_files_total", 0) > 2
            and not any("No upstream group summary" in str(item) for item in warnings or []),
            severity=severity,
            detail="Probe export should include real native summary evidence rather than metadata-only fallback.",
            metrics={
                "exported_subjects_total": result.get("exported_subjects_total") if isinstance(result, dict) else None,
                "source_files_total": result.get("source_files_total") if isinstance(result, dict) else None,
                "warnings": warnings,
            },
        )
    except Exception as exc:
        _check(checks, "report_exporter_probe_uses_native_outputs", False, severity=severity, detail=str(exc))


def validate_native_preproc_run(
    run_dir: str | Path,
    *,
    project_dir: str | Path | None = None,
    require_subject_ids: bool = False,
    require_report_chain: bool = False,
    probe_exporter: bool = False,
    require_fc_numerics: bool = False,
) -> dict[str, Any]:
    """Validate a native preprocessing run without modifying run artifacts."""

    run_path = Path(run_dir).expanduser().resolve()
    project_path = (
        Path(project_dir).expanduser().resolve()
        if project_dir is not None
        else run_path.parent.parent if run_path.parent.name == "preprocessing_native_runs" else run_path.parent
    )
    checks: list[Check] = []
    manifest_path = run_path / "native_full_run_manifest.json"
    group_path = run_path / "artifacts" / "group_summary" / "native_group_summary.json"
    validation_path = run_path / "artifacts" / "validation_report" / "native_preproc_validation_report.json"
    final_path = run_path / "artifacts" / "final_report" / "native_preproc_final_report.json"
    for name, path in {
        "manifest": manifest_path,
        "group_summary": group_path,
        "validation_report": validation_path,
        "final_report": final_path,
    }.items():
        _check(checks, f"required_file:{name}", path.exists() and path.stat().st_size > 0, detail=str(path))

    group = _read_json(group_path) if group_path.exists() else {}
    validation = _read_json(validation_path) if validation_path.exists() else {}
    final = _read_json(final_path) if final_path.exists() else {}
    _validate_artifact_payload(validation, checks)
    _validate_fc_numerical_contract(
        validation,
        final,
        checks,
        require_fc_numerics=require_fc_numerics,
    )
    _validate_subjects(
        group,
        validation,
        final,
        checks,
        require_subject_ids=require_subject_ids,
    )
    _validate_report_chain(
        project_path,
        run_path,
        checks,
        require_report_chain=require_report_chain,
        probe_exporter=probe_exporter,
    )
    failed_errors = [check for check in checks if check["status"] == "FAIL" and check["severity"] == "error"]
    failed_warnings = [check for check in checks if check["status"] == "FAIL" and check["severity"] == "warning"]
    status = "PASS" if not failed_errors and not failed_warnings else "WARNING" if not failed_errors else "FAIL"
    return {
        "ok": not failed_errors,
        "status": status,
        "run_dir": str(run_path),
        "project_dir": str(project_path),
        "stats": {
            "checks_total": len(checks),
            "failed_errors_total": len(failed_errors),
            "failed_warnings_total": len(failed_warnings),
        },
        "checks": checks,
        "next_actions": _next_actions(failed_errors, failed_warnings),
    }


def _next_actions(failed_errors: list[Check], failed_warnings: list[Check]) -> list[str]:
    names = {check["name"] for check in [*failed_errors, *failed_warnings]}
    actions: list[str] = []
    if "subject_ids_resolved" in names:
        actions.append("Resolve native preprocessing subject_id before claiming subject-level provenance is complete.")
    if "report_exporter_group_summary_source" in names or "report_exporter_probe_uses_native_outputs" in names:
        actions.append("Bridge native_group_summary/native final reports into the report exporter input discovery path.")
    if any(name.startswith("numeric:") for name in names):
        actions.append("Inspect failed numeric artifacts for NaN/Inf, all-zero data, shape mismatch, or matrix asymmetry.")
    if any(name.startswith("fc_") or name.startswith("roi_") for name in names):
        actions.append("Inspect ROI time-series, atlas labels, and Pearson FC matrix consistency before using FC results.")
    return actions or ["No blocking validation action detected."]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate native preprocessing run artifacts.")
    parser.add_argument("--run-dir", required=True, help="Native preprocessing run directory.")
    parser.add_argument("--project-dir", default=None, help="Project directory. Inferred from run-dir when omitted.")
    parser.add_argument("--require-subject-ids", action="store_true")
    parser.add_argument("--require-report-chain", action="store_true")
    parser.add_argument("--probe-exporter", action="store_true")
    parser.add_argument("--require-fc-numerics", action="store_true")
    args = parser.parse_args(argv)
    result = validate_native_preproc_run(
        args.run_dir,
        project_dir=args.project_dir,
        require_subject_ids=args.require_subject_ids,
        require_report_chain=args.require_report_chain,
        probe_exporter=args.probe_exporter,
        require_fc_numerics=args.require_fc_numerics,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
