from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path
from statistics import mean
from typing import Any

from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.tools.atlas_io import load_atlas_for_bold, sha256_file
from src.backend.app.tools.functional_connectivity_compute import compute_fc_backend


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _nifti_ext(path: Path) -> str:
    suffixes = "".join(path.suffixes).lower()
    if suffixes.endswith(".nii.gz"):
        return ".nii.gz"
    return path.suffix.lower()


def _find_filtered(subject_id: str, derivatives_dir: str) -> Path | None:
    func_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "func"
    if not func_dir.exists():
        return None
    preferred = [
        func_dir / f"filt_resid_swra{subject_id}_bold.nii",
        func_dir / f"filt_resid_swra{subject_id}_bold.nii.gz",
    ]
    for path in preferred:
        if path.exists():
            return path
    candidates = sorted(
        path
        for path in func_dir.glob("filt_resid*")
        if path.is_file() and _nifti_ext(path) in {".nii", ".nii.gz"}
    )
    return candidates[0] if candidates else None


def _safe_derivative_input(
    path: Path,
    subject_id: str,
    derivatives_dir: str,
    allowed_input_roots: tuple[str, ...] = (),
) -> bool:
    resolved = path.resolve()
    roots = (derivatives_dir, *allowed_input_roots)
    for root in roots:
        func_dir = (Path(root) / "rsfmri_preproc" / subject_id / "func").resolve()
        try:
            resolved.relative_to(func_dir)
        except ValueError:
            continue
        return path.name.startswith("filt_") and _nifti_ext(path) in {".nii", ".nii.gz"}
    return False


def _safe_atlas(
    path: Path,
    derivatives_dir: str,
    allowed_input_roots: tuple[str, ...] = (),
) -> bool:
    resolved = path.resolve()
    roots = [
        Path(derivatives_dir).resolve(),
        *(Path(root).resolve() for root in allowed_input_roots),
        Path("outputs/work").resolve(),
    ]
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _known_template_atlas_roots() -> list[Path]:
    return [
        _repo_root() / "third_party" / "DPABI_V8.2_240510" / "Templates",
    ]


def _relative_to_repo(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(_repo_root()).as_posix()
    except ValueError:
        return str(resolved)


def _safe_output_stem(path: Path, fallback: str) -> str:
    ext = _nifti_ext(path)
    name = path.name
    stem = name[: -len(ext)] if ext and name.lower().endswith(ext) else path.stem
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-")
    return cleaned[:80] if cleaned else fallback


def _is_known_template_resource(path: Path, *, allowed_suffixes: set[str]) -> bool:
    suffix = _nifti_ext(path) if ".nii" in "".join(path.suffixes).lower() else path.suffix.lower()
    if suffix not in allowed_suffixes:
        return False
    resolved = path.resolve()
    for root in _known_template_atlas_roots():
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _materialize_known_template_atlas(
    source_path: Path,
    derivatives_dir: str,
    warnings: list[str],
) -> dict[str, str] | None:
    if not _is_known_template_resource(source_path, allowed_suffixes={".nii", ".nii.gz"}):
        return None
    if not source_path.exists() or not source_path.is_file():
        raise ValueError(f"Template atlas not found: {source_path}")

    checksum = sha256_file(source_path)
    ext = _nifti_ext(source_path)
    stem = _safe_output_stem(source_path, "template")
    atlas_dir = Path(derivatives_dir) / "atlases" / "registered_templates"
    atlas_dir.mkdir(parents=True, exist_ok=True)
    dest = atlas_dir / f"{stem}_atlas_sha256-{checksum[:12]}{ext}"
    if not dest.exists() or sha256_file(dest) != checksum:
        shutil.copy2(source_path, dest)

    provenance = {
        "source_kind": "known_repo_template_atlas",
        "source_path": _relative_to_repo(source_path),
        "source_checksum": checksum,
        "registered_atlas_path": str(dest),
        "registered_atlas_checksum": sha256_file(dest),
        "safety": {
            "source_rawdata": False,
            "execution_input_is_derivative_copy": True,
            "arbitrary_absolute_path_allowed": False,
        },
    }
    provenance_path = atlas_dir / f"{stem}_atlas_sha256-{checksum[:12]}_provenance.json"
    atomic_write_json(provenance_path, provenance, schema_version=1)
    warnings.append(
        "Known repository template atlas was copied into derivatives before FC execution."
    )
    return {
        "atlas_path": str(dest),
        "provenance_path": str(provenance_path),
        "source_path": provenance["source_path"],
        "source_checksum": checksum,
    }


def _materialize_known_template_labels(
    source_path: Path,
    derivatives_dir: str,
    warnings: list[str],
) -> dict[str, str] | None:
    if not _is_known_template_resource(
        source_path, allowed_suffixes={".json", ".tsv", ".txt", ".csv"}
    ):
        return None
    if not source_path.exists() or not source_path.is_file():
        raise ValueError(f"Template atlas labels file not found: {source_path}")

    checksum = sha256_file(source_path)
    stem = _safe_output_stem(source_path, "labels")
    labels_dir = Path(derivatives_dir) / "atlases" / "registered_templates"
    labels_dir.mkdir(parents=True, exist_ok=True)
    dest = labels_dir / f"{stem}_labels_sha256-{checksum[:12]}{source_path.suffix.lower()}"
    if not dest.exists() or sha256_file(dest) != checksum:
        shutil.copy2(source_path, dest)
    warnings.append(
        "Known repository template labels file was copied into derivatives before FC execution."
    )
    return {
        "labels_path": str(dest),
        "source_path": _relative_to_repo(source_path),
        "source_checksum": checksum,
    }


def _write_tsv(path: Path, header: list[Any], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        writer.writerows(rows)


def _generate_atlas(shape: tuple[int, int, int], roi_count: int):
    import numpy as np

    nx, ny, nz = shape
    atlas = np.zeros(shape, dtype=np.int16)
    edges = np.linspace(0, nx, roi_count + 1).astype(int)
    definitions = []
    for idx in range(roi_count):
        start, end = int(edges[idx]), int(edges[idx + 1])
        if end <= start:
            continue
        atlas[start:end, :, :] = idx + 1
        definitions.append(
            {
                "label": idx + 1,
                "name": f"ROI_{idx + 1}",
                "strategy": "synthetic_x_chunk",
                "x_start": start,
                "x_end": end,
            }
        )
    return atlas, definitions


def _extract_roi_timeseries(
    data: Any, atlas: Any, labels: list[int]
) -> tuple[Any, dict[str, int], int, list[str]]:
    import numpy as np

    nt = int(data.shape[3])
    roi_timeseries = []
    voxel_counts: dict[str, int] = {}
    empty_count = 0
    warnings: list[str] = []
    for label in labels:
        mask = atlas == label
        voxel_count = int(np.count_nonzero(mask))
        voxel_counts[str(label)] = voxel_count
        if voxel_count == 0:
            empty_count += 1
            warnings.append(f"ROI {label} empty.")
            roi_timeseries.append(np.zeros((nt,), dtype=np.float64))
            continue
        ts = np.mean(data[mask, :], axis=0).astype(np.float64)
        roi_timeseries.append(np.where(np.isfinite(ts), ts, 0.0))
    stacked = np.vstack(roi_timeseries) if roi_timeseries else np.zeros((0, nt), dtype=np.float64)
    return stacked, voxel_counts, empty_count, warnings


def _write_qc_md(path: Path, qc: dict[str, Any]) -> None:
    lines = [
        f"# FC QC: {qc.get('subject_id')}",
        "",
        f"- OK: {qc.get('ok')}",
        f"- Status: {qc.get('fc_qc_status')}",
        f"- Stage status: {qc.get('stage_status')}",
        f"- Atlas grounded: {qc.get('atlas_grounded')}",
        f"- ROI count: {qc.get('roi_count')}",
        f"- Timepoints: {qc.get('timepoints')}",
        f"- Empty ROIs: {qc.get('empty_roi_count')}",
        f"- Timeseries finite: {qc.get('timeseries_finite_fraction')}",
        f"- Correlation finite: {qc.get('correlation_finite_fraction')}",
        f"- Symmetry diff: {qc.get('symmetry_max_abs_diff')}",
        f"- Seed map: {qc.get('seed_map_generated')}",
        "",
        "## Safety Note",
        "",
        "FC reads derivative files only and does not modify rawdata.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fail(
    subject_id: str,
    result_json: Path,
    qc_json: Path,
    qc_md: Path,
    errors: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    warnings = warnings or []
    qc = {
        "ok": False,
        "node_id": "functional_connectivity_qc_subject",
        "backend": "python",
        "subject_id": subject_id,
        "fc_qc_status": "FAIL",
        "stage_status": "failed",
        "atlas_grounded": False,
        "preview_only": False,
        "outputs": [str(qc_json), str(qc_md)],
        "warnings": warnings,
        "errors": errors,
    }
    result = {
        "ok": False,
        "node_id": "python_functional_connectivity_subject",
        "backend": "python",
        "subject_id": subject_id,
        "stage_status": "failed",
        "outputs": [str(result_json), str(qc_json), str(qc_md)],
        "warnings": warnings,
        "errors": errors,
    }
    atomic_write_json(result_json, result, schema_version=1)
    atomic_write_json(qc_json, qc, schema_version=1)
    _write_qc_md(qc_md, qc)
    return result


def run_python_functional_connectivity_subject(
    subject_id: str,
    derivatives_dir: str,
    roi_count: int = 4,
    atlas_path: str | None = None,
    labels_path: str | None = None,
    generate_seed_map: bool = False,
    input_nii: str | None = None,
    prefer_gpu: bool = False,
    require_gpu: bool = False,
    allowed_input_roots: tuple[str, ...] = (),
) -> dict[str, Any]:
    try:
        import nibabel as nib
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Missing dependency: nibabel and numpy are required.") from exc

    fc_dir = Path(derivatives_dir) / "rsfmri_fc" / subject_id
    qc_dir = Path(derivatives_dir) / "rsfmri_qc" / subject_id
    fc_dir.mkdir(parents=True, exist_ok=True)
    qc_dir.mkdir(parents=True, exist_ok=True)
    result_json = fc_dir / "fc_result.json"
    qc_json = qc_dir / "functional_connectivity_qc.json"
    qc_md = qc_dir / "functional_connectivity_qc.md"
    provenance_json = fc_dir / "functional_connectivity_provenance.json"
    warnings: list[str] = []
    errors: list[str] = []

    input_path = Path(input_nii) if input_nii else _find_filtered(subject_id, derivatives_dir)
    if not input_path:
        return _fail(
            subject_id, result_json, qc_json, qc_md, ["No filtered functional input found."]
        )
    if not input_path.exists():
        return _fail(
            subject_id,
            result_json,
            qc_json,
            qc_md,
            [f"Filtered functional input not found: {input_path}"],
        )
    if not _safe_derivative_input(
        input_path,
        subject_id,
        derivatives_dir,
        allowed_input_roots,
    ):
        return _fail(
            subject_id, result_json, qc_json, qc_md, [f"Unsafe filtered input: {input_path}"]
        )

    try:
        img = nib.load(str(input_path))
        data = img.get_fdata(dtype="float32")
        if data.ndim != 4:
            raise ValueError(f"Input NIfTI must be 4D. Got shape {data.shape}.")
        nx, ny, nz, nt = data.shape
        if nt < 3:
            raise ValueError(f"Functional connectivity requires at least 3 timepoints. Got {nt}.")

        if atlas_path:
            atlas_file = Path(atlas_path)
            if not _safe_atlas(atlas_file, derivatives_dir, allowed_input_roots):
                materialized = _materialize_known_template_atlas(
                    atlas_file, derivatives_dir, warnings
                )
                if materialized is None:
                    raise ValueError(f"Unsafe atlas: {atlas_file}")
                atlas_file = Path(materialized["atlas_path"])
            else:
                materialized = None

            labels_path_for_load = labels_path
            labels_materialized = None
            if labels_path and not _safe_atlas(
                Path(labels_path),
                derivatives_dir,
                allowed_input_roots,
            ):
                labels_materialized = _materialize_known_template_labels(
                    Path(labels_path), derivatives_dir, warnings
                )
                if labels_materialized is None:
                    raise ValueError(f"Unsafe atlas labels: {labels_path}")
                labels_path_for_load = labels_materialized["labels_path"]
            atlas_info = load_atlas_for_bold(
                atlas_path=atlas_file,
                bold_img=img,
                labels_path=labels_path_for_load,
            )
            atlas_data = atlas_info["atlas_data"]
            roi_definitions = atlas_info["roi_definitions"]
            atlas_file_for_output = atlas_info["atlas_file"]
            atlas_checksum = atlas_info["checksum"]
            warnings.extend(atlas_info.get("warnings", []))
            atlas_grounded = True
            preview_only = False
            stage_status = "succeeded"
            atlas_source = "registered_template_atlas" if materialized else "provided_atlas"
            atlas_template_source = materialized or {}
            labels_template_source = labels_materialized or {}
            labels_path_for_output = str(labels_path_for_load or "")
        else:
            atlas_data, roi_definitions = _generate_atlas((nx, ny, nz), int(roi_count))
            atlas_file = fc_dir / "synthetic_roi_atlas.nii"
            header = img.header.copy()
            try:
                header.set_data_shape(atlas_data.shape)
            except Exception:
                pass
            nib.save(
                nib.Nifti1Image(atlas_data.astype("int16"), affine=img.affine, header=header),
                str(atlas_file),
            )
            atlas_file_for_output = str(atlas_file)
            atlas_checksum = sha256_file(atlas_file)
            atlas_grounded = False
            preview_only = True
            stage_status = "preview_only"
            atlas_source = "synthetic_x_chunk"
            atlas_template_source = {}
            labels_template_source = {}
            labels_path_for_output = ""
            warnings.append(
                "Synthetic atlas generated; FC result is preview_only, not atlas-grounded."
            )

        labels = [int(item["label"]) for item in roi_definitions]
        names = [
            str(item.get("name") or f"ROI_{label}") for item, label in zip(roi_definitions, labels, strict=False)
        ]
        roi_timeseries, roi_voxel_counts, empty_roi_count, roi_warnings = _extract_roi_timeseries(
            data,
            atlas_data,
            labels,
        )
        warnings.extend(roi_warnings)

        compute = compute_fc_backend(
            data,
            atlas_data,
            generate_seed_map=generate_seed_map,
            prefer_gpu=prefer_gpu,
            require_gpu=require_gpu,
        )
        if not compute.get("ok"):
            raise ValueError(
                "; ".join(str(item) for item in compute.get("errors", []))
                or "FC computation failed."
            )
        corr = np.asarray(compute["correlation_matrix"], dtype=np.float64)
        fisher_z = np.asarray(compute["fisher_z_matrix"], dtype=np.float64)

        roi_timeseries_tsv = fc_dir / "roi_timeseries.tsv"
        _write_tsv(
            roi_timeseries_tsv,
            names,
            [
                [float(roi_timeseries[roi_index, time_index]) for roi_index in range(len(labels))]
                for time_index in range(nt)
            ],
        )

        labels_json = fc_dir / "labels.json"
        labels_tsv = fc_dir / "labels.tsv"
        labels_payload = {
            "subject_id": subject_id,
            "atlas_file": atlas_file_for_output,
            "labels_path": labels_path_for_output,
            "roi_count": len(labels),
            "labels": roi_definitions,
            "synthetic": not atlas_grounded,
            "atlas_grounded": atlas_grounded,
        }
        atomic_write_json(labels_json, labels_payload, schema_version=1)
        atomic_write_json(fc_dir / "roi_definitions.json", labels_payload, schema_version=1)
        _write_tsv(
            labels_tsv,
            ["label", "name", "strategy"],
            [
                [item["label"], item.get("name", ""), item.get("strategy", "")]
                for item in roi_definitions
            ],
        )

        corr_tsv = fc_dir / "correlation_matrix.tsv"
        corr_json = fc_dir / "correlation_matrix.json"
        corr_npy = fc_dir / "correlation_matrix.npy"
        fisher_tsv = fc_dir / "fisher_z_matrix.tsv"
        fisher_json = fc_dir / "fisher_z_matrix.json"
        fisher_npy = fc_dir / "fisher_z_matrix.npy"
        _write_tsv(
            corr_tsv,
            ["roi"] + names,
            [[names[idx]] + [float(value) for value in corr[idx]] for idx in range(len(names))],
        )
        _write_tsv(
            fisher_tsv,
            ["roi"] + names,
            [[names[idx]] + [float(value) for value in fisher_z[idx]] for idx in range(len(names))],
        )
        atomic_write_json(
            corr_json,
            {"subject_id": subject_id, "roi_names": names, "matrix": corr.tolist()},
            schema_version=1,
        )
        atomic_write_json(
            fisher_json,
            {"subject_id": subject_id, "roi_names": names, "matrix": fisher_z.tolist()},
            schema_version=1,
        )
        np.save(corr_npy, corr.astype("float32"))
        np.save(fisher_npy, fisher_z.astype("float32"))

        seed_corr_map = None
        seed_fisher_map = None
        seed_generated = False
        if generate_seed_map and compute.get("seed_correlation_map") is not None:
            seed_corr_map = fc_dir / "seed_correlation_map.nii"
            seed_fisher_map = fc_dir / "seed_fisher_z_map.nii"
            map_header = img.header.copy()
            try:
                map_header.set_data_shape(compute["seed_correlation_map"].shape)
            except Exception:
                pass
            nib.save(
                nib.Nifti1Image(
                    compute["seed_correlation_map"], affine=img.affine, header=map_header
                ),
                str(seed_corr_map),
            )
            nib.save(
                nib.Nifti1Image(compute["seed_fisher_z_map"], affine=img.affine, header=map_header),
                str(seed_fisher_map),
            )
            seed_generated = True
        elif generate_seed_map:
            warnings.append("Seed map requested but no valid seed map was produced.")

        timeseries_finite_fraction = (
            float(np.count_nonzero(np.isfinite(roi_timeseries)) / roi_timeseries.size)
            if roi_timeseries.size
            else 0.0
        )
        corr_finite_fraction = (
            float(np.count_nonzero(np.isfinite(corr)) / corr.size) if corr.size else 0.0
        )
        fisher_finite_fraction = (
            float(np.count_nonzero(np.isfinite(fisher_z)) / fisher_z.size) if fisher_z.size else 0.0
        )
        diagonal_mean = float(np.mean(np.diag(corr))) if corr.size else None
        symmetry_max_abs_diff = float(np.max(np.abs(corr - corr.T))) if corr.size else None
        fisher_diagonal_max_abs = (
            float(np.max(np.abs(np.diag(fisher_z)))) if fisher_z.size else None
        )

        fc_qc_status = "PASS"
        if len(labels) == 0:
            fc_qc_status = "FAIL"
            errors.append("No ROIs.")
        elif empty_roi_count > 0:
            fc_qc_status = "WARNING"
            warnings.append(f"{empty_roi_count} empty ROI(s).")
        elif (
            timeseries_finite_fraction < 1.0
            or corr_finite_fraction < 1.0
            or fisher_finite_fraction < 1.0
        ):
            fc_qc_status = "WARNING"
            warnings.append("Non-finite values detected.")
        elif diagonal_mean is not None and abs(diagonal_mean - 1.0) > 1e-5:
            fc_qc_status = "WARNING"
            warnings.append(f"Diagonal mean {diagonal_mean} != 1.0")
        elif symmetry_max_abs_diff is not None and symmetry_max_abs_diff > 1e-6:
            fc_qc_status = "WARNING"
            warnings.append(f"Symmetry diff {symmetry_max_abs_diff}")
        elif fisher_diagonal_max_abs is not None and fisher_diagonal_max_abs > 1e-6:
            fc_qc_status = "WARNING"
            warnings.append(f"Fisher-z diagonal max abs {fisher_diagonal_max_abs}")

        qc = {
            "ok": fc_qc_status != "FAIL",
            "node_id": "functional_connectivity_qc_subject",
            "backend": "python",
            "compute_backend": compute.get("backend", "cpu-numpy"),
            "subject_id": subject_id,
            "input_nii": str(input_path),
            "atlas_file": atlas_file_for_output,
            "atlas_source": atlas_source,
            "atlas_grounded": atlas_grounded,
            "preview_only": preview_only,
            "stage_status": stage_status if fc_qc_status != "FAIL" else "failed",
            "input_shape": list(data.shape),
            "atlas_shape": list(atlas_data.shape),
            "timepoints": int(nt),
            "roi_count": len(labels),
            "roi_names": names,
            "roi_voxel_counts": roi_voxel_counts,
            "empty_roi_count": empty_roi_count,
            "timeseries_finite_fraction": timeseries_finite_fraction,
            "correlation_matrix_shape": list(corr.shape),
            "correlation_finite_fraction": corr_finite_fraction,
            "fisher_z_finite_fraction": fisher_finite_fraction,
            "diagonal_mean": diagonal_mean,
            "symmetry_max_abs_diff": symmetry_max_abs_diff,
            "fisher_z_diagonal_max_abs": fisher_diagonal_max_abs,
            "seed_map_generated": seed_generated,
            "fc_qc_status": fc_qc_status,
            "outputs": [str(qc_json), str(qc_md)],
            "warnings": warnings,
            "errors": errors,
        }

        outputs = [
            atlas_file_for_output,
            str(labels_json),
            str(labels_tsv),
            str(fc_dir / "roi_definitions.json"),
            str(roi_timeseries_tsv),
            str(corr_tsv),
            str(corr_json),
            str(corr_npy),
            str(fisher_tsv),
            str(fisher_json),
            str(fisher_npy),
            str(provenance_json),
            str(result_json),
            str(qc_json),
            str(qc_md),
        ]
        if atlas_template_source.get("provenance_path"):
            outputs.append(str(atlas_template_source["provenance_path"]))
        if seed_corr_map:
            outputs.append(str(seed_corr_map))
        if seed_fisher_map:
            outputs.append(str(seed_fisher_map))

        provenance = {
            "algorithm_id": "roi_pearson_functional_connectivity",
            "algorithm_version": "1",
            "subject_id": subject_id,
            "input_nii": str(input_path),
            "input_checksum": sha256_file(input_path),
            "input_shape": list(data.shape),
            "atlas_file": atlas_file_for_output,
            "atlas_checksum": atlas_checksum,
            "atlas_source": atlas_source,
            "atlas_template_source": atlas_template_source,
            "atlas_grounded": atlas_grounded,
            "labels_path": labels_path_for_output,
            "labels_template_source": labels_template_source,
            "roi_count": len(labels),
            "correlation_method": "pearson",
            "fisher_z": True,
            "backend": "python",
            "compute_backend": compute.get("backend", "cpu-numpy"),
            "precision": "float32-output",
            "preview_only": preview_only,
            "outputs": outputs,
            "warnings": warnings,
        }
        atomic_write_json(provenance_json, provenance, schema_version=1)

        result = {
            "ok": fc_qc_status != "FAIL",
            "node_id": "python_functional_connectivity_subject",
            "backend": "python",
            "compute_backend": compute.get("backend", "cpu-numpy"),
            "subject_id": subject_id,
            "stage_status": qc["stage_status"],
            "fc_status": "atlas_grounded_computed" if atlas_grounded else "preview_only",
            "preview_only": preview_only,
            "atlas_grounded": atlas_grounded,
            "input_nii": str(input_path),
            "atlas_file": atlas_file_for_output,
            "labels_json": str(labels_json),
            "labels_tsv": str(labels_tsv),
            "roi_definitions": roi_definitions,
            "roi_timeseries_tsv": str(roi_timeseries_tsv),
            "correlation_matrix_tsv": str(corr_tsv),
            "correlation_matrix_json": str(corr_json),
            "correlation_matrix_npy": str(corr_npy),
            "fisher_z_matrix_tsv": str(fisher_tsv),
            "fisher_z_matrix_json": str(fisher_json),
            "fisher_z_matrix_npy": str(fisher_npy),
            "seed_correlation_map": str(seed_corr_map) if seed_corr_map else None,
            "seed_fisher_z_map": str(seed_fisher_map) if seed_fisher_map else None,
            "provenance_json": str(provenance_json),
            "qc": qc,
            "outputs": outputs,
            "warnings": warnings,
            "errors": errors,
        }
    except Exception as exc:
        return _fail(subject_id, result_json, qc_json, qc_md, [str(exc)], warnings)

    atomic_write_json(result_json, result, schema_version=1)
    atomic_write_json(qc_json, qc, schema_version=1)
    _write_qc_md(qc_md, qc)
    return result


def write_functional_connectivity_dataset_report(
    derivatives_dir: str, report_dir: str
) -> dict[str, Any]:
    derivatives = Path(derivatives_dir)
    report_out = Path(report_dir) / "rsfmri"
    report_out.mkdir(parents=True, exist_ok=True)
    qc_paths = sorted((derivatives / "rsfmri_qc").glob("*/functional_connectivity_qc.json"))
    subjects = []
    warnings: list[str] = []
    errors: list[str] = []
    for path in qc_paths:
        payload = _read_json(path)
        if not payload:
            warnings.append(f"Invalid: {path}")
            continue
        subjects.append(payload)
    subject_count = len(subjects)
    pass_count = sum(1 for item in subjects if item.get("fc_qc_status") == "PASS")
    warning_count = sum(1 for item in subjects if item.get("fc_qc_status") == "WARNING")
    fail_count = sum(1 for item in subjects if item.get("fc_qc_status") == "FAIL")
    preview_count = sum(1 for item in subjects if item.get("preview_only"))
    roi_counts = [
        float(item["roi_count"]) for item in subjects if item.get("roi_count") is not None
    ]
    empty_counts = [
        float(item["empty_roi_count"])
        for item in subjects
        if item.get("empty_roi_count") is not None
    ]
    summary = {
        "ok": subject_count > 0 and fail_count == 0,
        "node_id": "functional_connectivity_qc_dataset_report",
        "backend": "python",
        "subjects_total": subject_count,
        "subjects_pass": pass_count,
        "subjects_warning": warning_count,
        "subjects_fail": fail_count,
        "subjects_preview_only": preview_count,
        "mean_roi_count": float(mean(roi_counts)) if roi_counts else None,
        "mean_empty_roi_count": float(mean(empty_counts)) if empty_counts else None,
        "subjects": subjects,
        "warnings": warnings,
        "errors": errors,
    }
    summary_path = report_out / "functional_connectivity_qc_summary.json"
    report_path = report_out / "functional_connectivity_qc_report.md"
    atomic_write_json(summary_path, summary, schema_version=1)
    lines = [
        "# rs-fMRI FC QC Dataset Report",
        "",
        "## Summary",
        "",
        f"- Subjects: {subject_count}",
        f"- PASS: {pass_count}",
        f"- WARNING: {warning_count}",
        f"- FAIL: {fail_count}",
        f"- Preview-only: {preview_count}",
        f"- Mean ROI count: {summary['mean_roi_count']}",
        f"- Mean empty ROIs: {summary['mean_empty_roi_count']}",
        "",
        "## Subjects",
        "",
        "| Subject | Status | Stage | Atlas Grounded | ROI Count | Empty ROIs | Timepoints | Symmetry Diff |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in subjects:
        lines.append(
            f"| {item.get('subject_id')} | {item.get('fc_qc_status')} | {item.get('stage_status')} | "
            f"{item.get('atlas_grounded')} | {item.get('roi_count')} | {item.get('empty_roi_count')} | "
            f"{item.get('timepoints')} | {item.get('symmetry_max_abs_diff')} |"
        )
    lines += ["", "## Safety Note", "", "Derivative FC QC only. Does not modify rawdata."]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "node_id": "functional_connectivity_qc_dataset_report",
        "backend": "python",
        "outputs": [str(summary_path), str(report_path)],
        "metrics": {
            "subjects_total": subject_count,
            "subjects_pass": pass_count,
            "subjects_warning": warning_count,
            "subjects_fail": fail_count,
            "subjects_preview_only": preview_count,
        },
        "warnings": warnings,
        "errors": errors,
    }
