from __future__ import annotations

import re
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from src.backend.app.core.exceptions import SafetyError

from src.backend.app.tools.artifact_utils import (
    is_safe_artifact_id,
    read_json_artifact,
    sha256_file,
    write_json_artifact,
)

TEXT_EXTENSIONS = {".json", ".md", ".csv", ".tsv", ".txt", ".log", ".yaml", ".yml"}
EXCLUDED_EXTENSIONS = {".nii", ".gz", ".mat"}
_SUBJECT_RE = re.compile(r"sub-[A-Za-z0-9]+")
_NATIVE_SUMMARY_REL = Path("artifacts") / "group_summary" / "native_group_summary.json"
_NATIVE_VALIDATION_REL = (
    Path("artifacts") / "validation_report" / "native_preproc_validation_report.json"
)
_NATIVE_FINAL_REL = Path("artifacts") / "final_report" / "native_preproc_final_report.json"
_NATIVE_REPORT_ARTIFACT_TYPES = {
    "roi_timeseries",
    "fc_matrix",
    "motion_parameters",
    "fd_timeseries",
    "confounds",
}


def _now_id() -> str:
    return datetime.now().strftime("rsfmri_export_%Y%m%d_%H%M%S")


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _copy_file(source: Path, destination: Path) -> dict[str, Any]:
    _assert_windows_export_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "relative_path": None,
        "source_path": str(source),
        "size_bytes": int(destination.stat().st_size),
        "sha256": sha256_file(destination),
    }


def _safe_collect_files(base: Path, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    if not base.exists():
        return files
    for pattern in patterns:
        files.extend(sorted(base.glob(pattern)))
    seen = set()
    out = []
    for p in files:
        if p.is_file() and p not in seen:
            out.append(p)
            seen.add(p)
    return out


def _discover_subjects(derivatives: Path) -> list[str]:
    subjects = set()
    for bn in ["rsfmri_qc", "rsfmri_metrics", "rsfmri_fc", "rsfmri_confounds"]:
        b = derivatives / bn
        if not b.exists():
            continue
        for c in b.iterdir():
            if c.is_dir() and c.name.startswith("sub-"):
                subjects.add(c.name)
    return sorted(subjects)


def _read_json_payload(path: Path) -> dict[str, Any]:
    payload = read_json_artifact(path)
    if (
        isinstance(payload, dict)
        and "_schema_version" in payload
        and isinstance(payload.get("data"), dict)
    ):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _subject_ids_from_native_payloads(*payloads: dict[str, Any]) -> list[str]:
    subjects: set[str] = set()
    for payload in payloads:
        summaries = payload.get("subject_summaries")
        if isinstance(summaries, list):
            for item in summaries:
                if isinstance(item, dict):
                    sid = str(item.get("subject_id") or "").strip()
                    if sid and sid.lower() != "unknown":
                        if _SUBJECT_RE.fullmatch(sid) is None:
                            raise SafetyError("REPORT_EXPORT_SUBJECT_ID_INVALID", code="REPORT_EXPORT_SUBJECT_ID_INVALID")
                        subjects.add(sid)
        for match in _SUBJECT_RE.findall(str(payload)):
            subjects.add(match)
    return sorted(subjects)


def _native_run_score(run_dir: Path) -> float:
    candidates = [
        run_dir / _NATIVE_FINAL_REL,
        run_dir / _NATIVE_VALIDATION_REL,
        run_dir / _NATIVE_SUMMARY_REL,
    ]
    mtimes = [path.stat().st_mtime for path in candidates if path.exists()]
    return max(mtimes) if mtimes else 0.0


def _discover_latest_native_run(*roots: Path) -> Path | None:
    seen: set[Path] = set()
    runs: list[Path] = []
    for root in roots:
        root = root.expanduser()
        for run_root in (root / "preprocessing_native_runs",):
            if not run_root.exists():
                continue
            for run_dir in sorted(item for item in run_root.iterdir() if item.is_dir()):
                resolved = run_dir.resolve()
                if resolved not in seen and (run_dir / _NATIVE_SUMMARY_REL).is_file():
                    seen.add(resolved)
                    runs.append(run_dir)
        projects_root = root / "outputs" / "projects"
        if projects_root.exists():
            for project_dir in sorted(item for item in projects_root.iterdir() if item.is_dir()):
                run_root = project_dir / "preprocessing_native_runs"
                if not run_root.exists():
                    continue
                for run_dir in sorted(item for item in run_root.iterdir() if item.is_dir()):
                    resolved = run_dir.resolve()
                    if resolved not in seen and (run_dir / _NATIVE_SUMMARY_REL).is_file():
                        seen.add(resolved)
                        runs.append(run_dir)
    if not runs:
        return None
    return sorted(runs, key=_native_run_score)[-1]


def _native_stage_status_counts(*payloads: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for payload in payloads:
        for item in payload.get("stage_results") or []:
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage_id") or "unknown")
            status = str(item.get("status") or "").lower()
            if status in {"succeeded", "metadata_only"}:
                bucket = "PASS"
            elif status in {"warning", "simplified"}:
                bucket = "WARNING"
            elif status == "skipped":
                bucket = "MISSING"
            else:
                bucket = "FAIL"
            counts.setdefault(stage, {"PASS": 0, "WARNING": 0, "FAIL": 0, "MISSING": 0})[
                bucket
            ] += 1
    return counts


def _native_warning_error_totals(*payloads: dict[str, Any]) -> tuple[int, int]:
    warnings = 0
    errors = 0
    for payload in payloads:
        for item in payload.get("stage_results") or []:
            if not isinstance(item, dict):
                continue
            warnings += len(item.get("warnings") or [])
            errors += len(item.get("errors") or []) + len(item.get("blocking_issues") or [])
        validation = payload.get("validation_summary")
        if isinstance(validation, dict):
            summary = validation.get("summary")
            if isinstance(summary, dict):
                errors += int(summary.get("artifact_failed_count") or 0)
                errors += int(summary.get("truthfulness_failed_count") or 0)
                errors += int(summary.get("safety_failed_count") or 0)
    return warnings, errors


def _native_subject_stage_payloads(run_dir: Path, subjects: list[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for subject_id in subjects:
        candidates = [
            run_dir / subject_id / "native_full_run_manifest.json",
            run_dir / subject_id / _NATIVE_FINAL_REL,
        ]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            payload = _read_json_payload(candidate)
            if isinstance(payload.get("stage_results"), list):
                payloads.append(payload)
                break
    return payloads


def _native_artifact_sources(
    validation_payload: dict[str, Any], subjects: list[str]
) -> list[tuple[Path, Path, str]]:
    sid = subjects[0] if subjects else "unassigned"
    sources: list[tuple[Path, Path, str]] = []
    artifacts = validation_payload.get("artifact_validation", {}).get("artifacts", [])
    if not isinstance(artifacts, list):
        return sources
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        artifact_type = str(item.get("artifact_type") or "")
        if artifact_type not in _NATIVE_REPORT_ARTIFACT_TYPES:
            continue
        src = Path(str(item.get("path") or ""))
        if not src.is_file() or src.suffix.lower() in EXCLUDED_EXTENSIONS:
            continue
        if artifact_type in {"roi_timeseries", "fc_matrix"}:
            dst = Path("fc") / sid / src.name
            category = "functional_connectivity"
        else:
            dst = Path("subjects") / sid / "confounds" / src.name
            category = "confounds"
        sources.append((src, dst, category))
    return sources


def _write_native_group_summary_bridge(
    pkg: Path, run_dir: Path, subjects: list[str]
) -> tuple[dict[str, Any], list[Path], list[tuple[Path, Path, str]], list[str], list[str]]:
    native_group = _read_json_payload(run_dir / _NATIVE_SUMMARY_REL)
    native_validation = (
        _read_json_payload(run_dir / _NATIVE_VALIDATION_REL)
        if (run_dir / _NATIVE_VALIDATION_REL).exists()
        else {}
    )
    native_final = (
        _read_json_payload(run_dir / _NATIVE_FINAL_REL)
        if (run_dir / _NATIVE_FINAL_REL).exists()
        else {}
    )
    resolved_subjects = subjects or _subject_ids_from_native_payloads(
        native_group, native_validation, native_final
    )
    stage_payloads = _native_subject_stage_payloads(run_dir, resolved_subjects)
    if not stage_payloads and isinstance(native_final.get("stage_results"), list):
        stage_payloads = [native_final]
    warnings_total, errors_total = _native_warning_error_totals(*stage_payloads)
    summary = {
        "ok": errors_total == 0,
        "summary_type": "native_preproc_group_summary_bridge",
        "capability_level": "computed",
        "native_run_dir": str(run_dir),
        "subjects_total": int(native_group.get("subject_count") or len(resolved_subjects)),
        "subjects_with_any_qc": int(
            native_group.get("completed_subject_count") or len(resolved_subjects)
        ),
        "warnings_total": warnings_total,
        "errors_total": errors_total,
        "stage_status_counts": _native_stage_status_counts(*stage_payloads),
        "warnings": list(native_group.get("limitations") or []),
        "errors": [],
    }
    gd = pkg / "summary" / "group_summary"
    gd.mkdir(parents=True, exist_ok=True)
    td = pkg / "tables"
    td.mkdir(parents=True, exist_ok=True)
    dashboard = {
        "ok": summary["ok"],
        "summary_type": summary["summary_type"],
        "capability_level": "computed",
        "subjects": [
            {"subject_id": sid, "summary_status": "native_preproc_computed"}
            for sid in resolved_subjects
        ],
        "warnings": summary["warnings"],
        "errors": [],
    }
    table_text = "subject_id,summary_status\n" + "".join(
        f"{sid},native_preproc_computed\n" for sid in resolved_subjects
    )
    report_text = "\n".join(
        [
            "# rs-fMRI Group Summary",
            "",
            "Native preprocessing outputs were used as the upstream group-summary evidence for this report package.",
            "",
            "## Subjects",
            "",
            f"- Subjects: {len(resolved_subjects)}",
            "",
            "## Native Validation",
            "",
            f"- Overall status: {native_validation.get('overall_status', 'unknown')}",
            f"- Artifact failed count: {native_validation.get('summary', {}).get('artifact_failed_count', 'unknown')}",
            f"- Safety failed count: {native_validation.get('summary', {}).get('safety_failed_count', 'unknown')}",
            "",
        ]
    )
    dataset_path = write_json_artifact(gd / "dataset_summary.json", summary)
    dashboard_path = write_json_artifact(gd / "dashboard_data.json", dashboard)
    report_path = gd / "dataset_summary_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    summary_table = gd / "subject_metrics_table.csv"
    summary_table.write_text(table_text, encoding="utf-8")
    table_path = td / "subject_metrics_table.csv"
    table_path.write_text(table_text, encoding="utf-8")
    sources = [
        (
            run_dir / _NATIVE_SUMMARY_REL,
            Path("summary") / "group_summary" / "native_group_summary.json",
            "native_group_summary",
        )
    ]
    if (run_dir / _NATIVE_VALIDATION_REL).is_file():
        sources.append(
            (
                run_dir / _NATIVE_VALIDATION_REL,
                Path("summary") / "group_summary" / "native_preproc_validation_report.json",
                "native_validation_report",
            )
        )
    if (run_dir / _NATIVE_FINAL_REL).is_file():
        sources.append(
            (
                run_dir / _NATIVE_FINAL_REL,
                Path("summary") / "group_summary" / "native_preproc_final_report.json",
                "native_final_report",
            )
        )
    sources.extend(_native_artifact_sources(native_validation, resolved_subjects))
    for subject_id in resolved_subjects:
        subject_validation_path = run_dir / subject_id / _NATIVE_VALIDATION_REL
        if not subject_validation_path.is_file():
            continue
        subject_validation = _read_json_payload(subject_validation_path)
        sources.extend(_native_artifact_sources(subject_validation, [subject_id]))
    bridge_warnings = [f"Using native preprocessing run as report evidence: {run_dir.name}"]
    return (
        summary,
        [dataset_path, dashboard_path, report_path, summary_table, table_path],
        sources,
        bridge_warnings,
        resolved_subjects,
    )


def _stage_summary(gs: dict[str, Any] | None) -> str:
    if not gs:
        return "No group summary."
    counts = gs.get("stage_status_counts", {})
    if not isinstance(counts, dict):
        return "No stage counts."
    lines = ["| Stage | PASS | WARNING | FAIL | MISSING |", "|---|---:|---:|---:|---:|"]
    for stage, item in counts.items():
        item = item or {}
        lines.append(
            f"| {stage} | {item.get('PASS', 0)} | {item.get('WARNING', 0)} | {item.get('FAIL', 0)} | {item.get('MISSING', 0)} |"
        )
    return "\n".join(lines)


def _write_readme(path: Path, export_id: str, summary: dict[str, Any]) -> None:
    lines = [
        f"# rs-fMRI Report Package: {export_id}",
        "",
        "Engineering validation outputs for an rs-fMRI research pipeline.",
        "",
        "## Contents",
        "",
        "- `MANIFEST.json`: file manifest with checksums",
        "- `index.md`: human-readable index",
        "- `export_summary.json`: exporter summary",
        "- `summary/`: group and stage-level reports",
        "- `subjects/`: subject-level QC and confounds",
        "- `metrics/`: subject metrics result JSON",
        "- `fc/`: functional connectivity results",
        "- `contracts/`: DPABI and GPU backend contracts",
        "- `pipeline_runs/`: pipeline run summaries",
        "- `tables/`: CSV/TSV tables",
        "- `checksums/SHA256SUMS.txt`: checksums",
        "",
        "## Safety",
        "",
        "Generated from reviewed derivative outputs. The package excludes rawdata and does not make clinical conclusions.",
        "",
        "## Export Summary",
        "",
        f"- Exported subjects: {summary.get('exported_subjects')}",
        f"- Exported files: {summary.get('exported_files_total')}",
        f"- Excluded files: {summary.get('excluded_files_total')}",
        f"- Warnings: {len(summary.get('warnings', []))}",
        f"- Errors: {len(summary.get('errors', []))}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_index(path: Path, export_id: str, gs: dict[str, Any] | None, es: dict[str, Any]) -> None:
    lines = [
        "# rs-fMRI Dataset Report Index",
        "",
        f"- Export ID: `{export_id}`",
        f"- Created: `{es.get('created_at')}`",
        f"- Subjects: {es.get('exported_subjects')}",
        f"- Files: {es.get('exported_files_total')}",
        "",
        "## Dataset Overview",
        "",
    ]
    if gs:
        lines += [
            f"- Subjects total: {gs.get('subjects_total')}",
            f"- With QC: {gs.get('subjects_with_any_qc')}",
            f"- Warnings: {gs.get('warnings_total')}",
            f"- Errors: {gs.get('errors_total')}",
        ]
    else:
        lines.append("Group summary not available.")
    lines += [
        "",
        "## Stage Status",
        "",
        _stage_summary(gs),
        "",
        "## Included Files",
        "",
        "- `summary/group_summary/dataset_summary.json`",
        "- `summary/group_summary/dashboard_data.json`",
        "- `summary/group_summary/dataset_summary_report.md`",
        "- `tables/subject_metrics_table.csv`",
        "- `contracts/`",
        "",
        "## Limitations",
        "",
        "- Engineering validation only, not clinical report.",
        "- No rawdata included.",
        "- No statistical inference performed.",
        "- No SPM/MATLAB/DPABI/GPU execution.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _zip_directory(source_dir: Path, archive: BinaryIO) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(source_dir.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(source_dir).as_posix())


def _package_relative_path(pkg: Path, path: Path) -> str:
    return path.relative_to(pkg).as_posix()


def _register(
    copied: list[dict[str, Any]], pkg: Path, src: Path, dest: Path, category: str, project_root: Path
) -> None:
    _assert_project_source(src, project_root)
    if not dest.resolve().is_relative_to(pkg.resolve()):
        raise SafetyError("REPORT_EXPORT_PATH_OUTSIDE_PACKAGE", code="REPORT_EXPORT_PATH_OUTSIDE_PACKAGE")
    info = _copy_file(src, dest)
    info["relative_path"] = _package_relative_path(pkg, dest)
    info["category"] = category
    copied.append(info)


def _register_generated(copied: list[dict[str, Any]], pkg: Path, dest: Path, category: str) -> None:
    copied.append(
        {
            "relative_path": _package_relative_path(pkg, dest),
            "source_path": None,
            "size_bytes": int(dest.stat().st_size),
            "sha256": sha256_file(dest),
            "category": category,
        }
    )


def _write_missing_group_summary(
    pkg: Path, subjects: list[str]
) -> tuple[dict[str, Any], list[Path], str]:
    warning = (
        "No upstream group summary artifact was found; exported a metadata-only fallback summary."
    )
    gd = pkg / "summary" / "group_summary"
    gd.mkdir(parents=True, exist_ok=True)
    td = pkg / "tables"
    td.mkdir(parents=True, exist_ok=True)
    summary = {
        "ok": False,
        "summary_type": "metadata_only_missing_group_summary",
        "capability_level": "metadata_only",
        "subjects_total": len(subjects),
        "subjects_with_any_qc": 0,
        "warnings_total": 1,
        "errors_total": 0,
        "stage_status_counts": {},
        "warnings": [warning],
        "errors": [],
        "notes": [
            "This file is a package fallback because the reviewed group summary artifact was absent.",
            "It preserves export provenance but does not replace group-level QC computation.",
        ],
    }
    dashboard = {
        "ok": False,
        "summary_type": summary["summary_type"],
        "capability_level": "metadata_only",
        "subjects": [
            {"subject_id": sid, "summary_status": "missing_group_summary"} for sid in subjects
        ],
        "warnings": [warning],
        "errors": [],
    }
    table_text = "subject_id,summary_status\n" + "".join(
        f"{sid},missing_group_summary\n" for sid in subjects
    )
    report_text = "\n".join(
        [
            "# rs-fMRI Group Summary",
            "",
            "A reviewed group summary artifact was not available when this report package was exported.",
            "",
            "This metadata-only fallback records the missing evidence so package export can complete without inventing group statistics.",
            "",
            "## Subjects",
            "",
            f"- Discovered subjects: {len(subjects)}",
            "",
            "## Warning",
            "",
            f"- {warning}",
            "",
        ]
    )
    dataset_path = write_json_artifact(gd / "dataset_summary.json", summary)
    dashboard_path = write_json_artifact(gd / "dashboard_data.json", dashboard)
    report_path = gd / "dataset_summary_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    summary_table = gd / "subject_metrics_table.csv"
    summary_table.write_text(table_text, encoding="utf-8")
    table_path = td / "subject_metrics_table.csv"
    table_path.write_text(table_text, encoding="utf-8")
    return summary, [dataset_path, dashboard_path, report_path, summary_table, table_path], warning


def _write_checksums(
    pkg: Path, copied: list[dict[str, Any]], manifest_path: Path, checksums_path: Path
) -> None:
    checksums_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{item['sha256']}  {item['relative_path']}" for item in copied]
    if manifest_path.exists():
        lines.append(f"{sha256_file(manifest_path)}  {manifest_path.name}")
    checksums_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _assert_windows_export_path(path: Path) -> None:
    length = len(str(path.resolve()).encode("utf-16-le")) // 2
    if os.name == "nt" and length >= 260:
        raise SafetyError("REPORT_EXPORT_PATH_TOO_LONG", code="REPORT_EXPORT_PATH_TOO_LONG", details={"path_length": length, "file_name": path.name})


def _assert_project_source(path: Path, project_root: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(project_root):
        raise SafetyError("REPORT_EXPORT_SOURCE_OUTSIDE_PROJECT", code="REPORT_EXPORT_SOURCE_OUTSIDE_PROJECT")
    if "rawdata" in {part.casefold() for part in resolved.relative_to(project_root).parts}:
        raise SafetyError("REPORT_EXPORT_RAWDATA_FORBIDDEN", code="REPORT_EXPORT_RAWDATA_FORBIDDEN")


def export_rsfmri_report_package(
    *, project_dir: str,
    derivatives_dir: str = "./derivatives", reports_dir: str = "./reports",
    work_dir: str = "./work", exports_dir: str = "./exports",
    export_id: str | None = None, include_subject_qc: bool = True,
    include_metrics: bool = True, include_fc: bool = True,
    include_contracts: bool = True, include_pipeline_runs: bool = True,
) -> dict[str, Any]:
    """Build one project-scoped package, rolling back only this newly owned output."""
    project = Path(project_dir).expanduser().resolve()
    eid = export_id or _now_id()
    if not is_safe_artifact_id(eid):
        raise SafetyError("REPORT_EXPORT_ID_INVALID", code="REPORT_EXPORT_ID_INVALID")
    roots = []
    for value in (derivatives_dir, reports_dir, work_dir, exports_dir):
        candidate = Path(value).expanduser()
        resolved = (candidate if candidate.is_absolute() else project / candidate).resolve()
        _assert_project_source(resolved, project)
        roots.append(resolved)
    d, rpt, work, exports = roots
    relative = exports.relative_to(project)
    if not relative.parts or relative.parts[0] not in {"work", "logs", "reports", "derivatives", "exports", "outputs"}:
        raise SafetyError("REPORT_EXPORT_WRITE_ROOT_INVALID", code="REPORT_EXPORT_WRITE_ROOT_INVALID")
    proot = (exports / "rsfmri_report_package").resolve()
    if not proot.is_relative_to(exports):
        raise SafetyError("REPORT_EXPORT_WRITE_ROOT_INVALID", code="REPORT_EXPORT_WRITE_ROOT_INVALID")
    _assert_project_source(proot, project)
    package = proot / eid
    archive = proot / (eid + ".zip")
    if package.exists() or archive.exists() or package.is_symlink() or archive.is_symlink():
        raise SafetyError("REPORT_EXPORT_ALREADY_EXISTS", code="REPORT_EXPORT_ALREADY_EXISTS")
    _assert_windows_export_path(package / "summary/group_summary/native_preproc_validation_report.json")
    owned = False
    archive_owned = False
    try:
        proot.mkdir(parents=True, exist_ok=True)
        package.mkdir(exist_ok=False)
        owned = True
        result = _build_report_package(
            project_dir=str(project), derivatives_dir=str(d), reports_dir=str(rpt),
            work_dir=str(work), exports_dir=str(exports), export_id=eid,
            include_subject_qc=include_subject_qc, include_metrics=include_metrics,
            include_fc=include_fc, include_contracts=include_contracts,
            include_pipeline_runs=include_pipeline_runs,
        )
        with archive.open("xb") as archive_handle:
            archive_owned = True
            _zip_directory(package, archive_handle)
        with zipfile.ZipFile(archive) as handle:
            if handle.testzip() is not None:
                raise OSError("REPORT_EXPORT_ZIP_CORRUPT")
        result["zip_size_bytes"] = archive.stat().st_size
        return result
    except BaseException as exc:
        if owned:
            assert package.resolve().parent == proot.resolve()
            try:
                if archive_owned and archive.exists():
                    archive.unlink()
                shutil.rmtree(package)
            except OSError as cleanup_error:
                raise SafetyError("REPORT_EXPORT_CLEANUP_FAILED", code="REPORT_EXPORT_CLEANUP_FAILED", details={
                    "package_relative_path": package.relative_to(project).as_posix(),
                    "original_code": getattr(exc, "code", type(exc).__name__),
                    "cleanup_errno": cleanup_error.errno,
                }) from exc
        if isinstance(exc, OSError):
            raise SafetyError("REPORT_EXPORT_WRITE_FAILED", code="REPORT_EXPORT_WRITE_FAILED", details={"errno": exc.errno, "winerror": getattr(exc, "winerror", None)}) from exc
        raise


def _build_report_package(
    project_dir: str,
    derivatives_dir: str = "./derivatives",
    reports_dir: str = "./reports",
    work_dir: str = "./work",
    exports_dir: str = "./exports",
    export_id: str | None = None,
    include_subject_qc: bool = True,
    include_metrics: bool = True,
    include_fc: bool = True,
    include_contracts: bool = True,
    include_pipeline_runs: bool = True,
) -> dict[str, Any]:
    d = Path(derivatives_dir)
    rpt = Path(reports_dir)
    w = Path(work_dir)
    e = Path(exports_dir)
    eid = export_id or _now_id()
    proot = e / "rsfmri_report_package"
    pkg = proot / eid
    zp = proot / f"{eid}.zip"
    pkg.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    errors: list[str] = []
    copied: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    subs = _discover_subjects(d)
    native_run = _discover_latest_native_run(Path(project_dir))
    if native_run is not None:
        _assert_project_source(native_run, Path(project_dir))
        for relative in (_NATIVE_SUMMARY_REL, _NATIVE_VALIDATION_REL, _NATIVE_FINAL_REL):
            _assert_project_source(native_run / relative, Path(project_dir))

    # Group summary
    gb = rpt / "rsfmri" / "group_summary"
    gfs = _safe_collect_files(gb, ["*.json", "*.md", "*.csv"])
    fallback_group_summary: dict[str, Any] | None = None
    if not gfs and native_run is not None:
        fallback_group_summary, generated, source_specs, bridge_warnings, native_subjects = (
            _write_native_group_summary_bridge(pkg, native_run, subs)
        )
        subs = native_subjects or subs
        warnings.extend(bridge_warnings)
        for path in generated:
            _register_generated(
                copied, pkg, path, "group_summary" if "summary" in path.parts else "table"
            )
        for src, rel, category in source_specs:
            if src.is_file():
                _register(copied, pkg, src, pkg / rel, category, project_root=Path(project_dir))
    elif not gfs:
        fallback_group_summary, generated, warning = _write_missing_group_summary(pkg, subs)
        warnings.append(warning)
        for path in generated:
            _register_generated(
                copied,
                pkg,
                path,
                "group_summary_metadata_only" if "summary" in path.parts else "table_metadata_only",
            )
    for s in gfs:
        dst = pkg / "summary" / "group_summary" / s.name
        _register(copied, pkg, s, dst, "group_summary", project_root=Path(project_dir))
        if s.name == "subject_metrics_table.csv":
            _register(copied, pkg, s, pkg / "tables" / "subject_metrics_table.csv", "table", project_root=Path(project_dir))
    for s in _safe_collect_files(rpt / "rsfmri", ["*.json", "*.md"]):
        if "group_summary" in s.parts:
            continue
        _register(copied, pkg, s, pkg / "summary" / "stage_reports" / s.name, "stage_report", project_root=Path(project_dir))

    # Subject QC & confounds
    if include_subject_qc:
        for sid in subs:
            for s in _safe_collect_files(d / "rsfmri_qc" / sid, ["*.json", "*.md"]):
                _register(copied, pkg, s, pkg / "subjects" / sid / "qc" / s.name, "subject_qc", project_root=Path(project_dir))
            for s in _safe_collect_files(
                d / "rsfmri_confounds" / sid, ["*.json", "*.tsv", "*.csv", "*.md"]
            ):
                _register(
                    copied, pkg, s, pkg / "subjects" / sid / "confounds" / s.name, "confounds"
                , project_root=Path(project_dir))

    # Metrics
    if include_metrics:
        for sid in subs:
            md = d / "rsfmri_metrics" / sid
            for s in _safe_collect_files(md, ["*.json", "*.md", "*.tsv", "*.csv"]):
                _register(copied, pkg, s, pkg / "metrics" / sid / s.name, "metrics", project_root=Path(project_dir))
            if md.exists():
                for s in sorted(md.glob("*")):
                    if s.is_file() and s.suffix in EXCLUDED_EXTENSIONS:
                        excluded.append(
                            {"source_path": str(s), "reason": "Binary metric image excluded."}
                        )

    # FC
    if include_fc:
        for sid in subs:
            fd = d / "rsfmri_fc" / sid
            for s in _safe_collect_files(fd, ["*.json", "*.tsv", "*.csv", "*.md"]):
                _register(copied, pkg, s, pkg / "fc" / sid / s.name, "functional_connectivity", project_root=Path(project_dir))
            if fd.exists():
                for s in sorted(fd.glob("*")):
                    if s.is_file() and s.suffix in EXCLUDED_EXTENSIONS:
                        excluded.append(
                            {"source_path": str(s), "reason": "Binary FC image excluded."}
                        )

    # Contracts
    if include_contracts:
        dc = _safe_collect_files(w / "dpabi" / "contracts", ["*.json"])
        gc = _safe_collect_files(w / "gpu" / "contracts", ["*.json"])
        if not dc and not gc:
            warnings.append("No DPABI/GPU contracts found.")
        for s in dc:
            _register(copied, pkg, s, pkg / "contracts" / "dpabi" / s.name, "dpabi_contract", project_root=Path(project_dir))
        for s in gc:
            _register(copied, pkg, s, pkg / "contracts" / "gpu" / s.name, "gpu_contract", project_root=Path(project_dir))

    # Pipeline runs
    if include_pipeline_runs:
        rps = (
            sorted((w / "pipeline_runs").glob("*/summary.json"))
            if (w / "pipeline_runs").exists()
            else []
        )
        if not rps:
            warnings.append("No pipeline run summaries found.")
        for s in rps[-50:]:
            _register(
                copied,
                pkg,
                s,
                pkg / "pipeline_runs" / f"{s.parent.name}_summary.json",
                "pipeline_run",
             project_root=Path(project_dir))

    rp = pkg / "README.md"
    ip = pkg / "index.md"
    ep = pkg / "export_summary.json"
    mp = pkg / "MANIFEST.json"
    csp = pkg / "checksums" / "SHA256SUMS.txt"
    gs_path = pkg / "summary" / "group_summary" / "dataset_summary.json"
    gs = read_json_artifact(gs_path) if gs_path.exists() else fallback_group_summary
    source_files_total = sum(1 for item in copied if item.get("source_path"))
    if source_files_total == 0:
        warnings.append("No source files exported.")
    es = {
        "ok": source_files_total > 0,
        "node_id": "rsfmri_report_exporter",
        "backend": "python",
        "export_id": eid,
        "package_dir": str(pkg),
        "zip_path": str(zp),
        "created_at": _iso_now(),
        "exported_subjects": subs,
        "exported_subjects_total": len(subs),
        "source_files_total": source_files_total,
        "exported_files_total": len(copied) + 3,
        "excluded_files_total": len(excluded),
        "warnings": warnings,
        "errors": errors,
        "zip_size_bytes": None,
        "outputs": [str(pkg), str(zp), str(mp), str(rp), str(ip), str(ep), str(csp)],
    }
    _write_readme(rp, eid, es)
    _write_index(ip, eid, gs, es)
    for gp, cat in [(rp, "package_readme"), (ip, "package_index")]:
        _register_generated(copied, pkg, gp, cat)
    es["exported_files_total"] = len(copied) + 1
    write_json_artifact(ep, es)
    _register_generated(copied, pkg, ep, "export_summary")

    source_roots = {"derivatives": str(d), "reports": str(rpt), "work": str(w)}
    if native_run is not None:
        source_roots["native_preproc_run"] = str(native_run)
    manifest = {
        "package_id": eid,
        "export_id": eid,
        "created_at": es["created_at"],
        "source_roots": source_roots,
        "safety": {
            "rawdata_included": False,
            "rawdata_modified": False,
            "derivatives_modified": False,
            "reports_modified": False,
            "work_modified": False,
            "spm_executed": False,
            "matlab_executed": False,
            "dpabi_executed": False,
            "gpu_executed": False,
            "files_deleted": False,
            "clinical_conclusions_generated": False,
            "statistical_inference_performed": False,
        },
        "files": copied,
        "excluded_files": excluded,
        "warnings": warnings,
        "errors": errors,
    }
    write_json_artifact(mp, manifest)
    _write_checksums(pkg, copied, mp, csp)
    return es


def _read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return read_json_artifact(path)
    except Exception as exc:
        return {"_read_error": str(exc)}


def _summarize_export_dir(pkg: Path) -> dict[str, Any]:
    s = _read_json_if_present(pkg / "export_summary.json") or {}
    zip_path = pkg.parent / f"{pkg.name}.zip"
    zip_size = int(zip_path.stat().st_size) if zip_path.exists() else None
    missing = []
    if not (pkg / "export_summary.json").exists():
        missing.append("export_summary.json")
    if not (pkg / "MANIFEST.json").exists():
        missing.append("MANIFEST.json")
    if not zip_path.exists():
        missing.append(f"{pkg.name}.zip")
    entry = {
        "export_id": pkg.name,
        "package_dir": str(pkg),
        "zip_path": str(zip_path),
        "zip_size_bytes": s.get("zip_size_bytes") or zip_size,
        "created_at": s.get("created_at"),
        "ok": s.get("ok", False) and not missing,
        "exported_files_total": s.get("exported_files_total"),
        "exported_subjects_total": s.get("exported_subjects_total"),
    }
    if missing:
        entry["missing_evidence"] = missing
    if s.get("_read_error"):
        entry["read_error"] = s["_read_error"]
        entry["ok"] = False
    return entry


def _is_readable_export(pkg: Path) -> bool:
    s = _read_json_if_present(pkg / "export_summary.json")
    return (
        bool(s)
        and not s.get("_read_error")
        and (pkg / "MANIFEST.json").exists()
        and (pkg.parent / f"{pkg.name}.zip").exists()
    )


def _read_export(pkg: Path) -> dict[str, Any]:
    s = _read_json_if_present(pkg / "export_summary.json") or {}
    m = _read_json_if_present(pkg / "MANIFEST.json")
    ip = pkg / "index.md"
    rp = pkg / "README.md"
    zip_path = pkg.parent / f"{pkg.name}.zip"
    zip_size = int(zip_path.stat().st_size) if zip_path.exists() else None
    if s and not s.get("_read_error"):
        s = {**s, "zip_size_bytes": s.get("zip_size_bytes") or zip_size}
    missing = [
        name for name in ("export_summary.json", "MANIFEST.json") if not (pkg / name).exists()
    ]
    if not zip_path.exists():
        missing.append(f"{pkg.name}.zip")
    result = {
        "ok": bool(s) and not s.get("_read_error") and not missing,
        "export_id": pkg.name,
        "package_dir": str(pkg),
        "zip_path": str(zip_path),
        "zip_size_bytes": zip_size,
        "export_summary": s,
        "manifest": m,
        "index_md": ip.read_text(encoding="utf-8") if ip.exists() else None,
        "readme_md": rp.read_text(encoding="utf-8") if rp.exists() else None,
    }
    if missing:
        result["missing_evidence"] = missing
    return result


def list_rsfmri_report_exports(exports_dir: str = "./exports") -> dict[str, Any]:
    proot = Path(exports_dir) / "rsfmri_report_package"
    packages = []
    if proot.exists():
        for c in sorted(proot.iterdir()):
            if c.is_dir():
                packages.append(_summarize_export_dir(c))
    return {"ok": True, "exports_total": len(packages), "exports": packages}


def get_latest_rsfmri_report_export(exports_dir: str = "./exports") -> dict[str, Any]:
    proot = Path(exports_dir) / "rsfmri_report_package"
    if not proot.exists():
        return {"ok": False, "errors": ["No exports found."], "warnings": []}
    pkgs = sorted([c for c in proot.iterdir() if c.is_dir() and _is_readable_export(c)])
    if not pkgs:
        return {"ok": False, "errors": ["No complete report exports found."], "warnings": []}
    return _read_export(pkgs[-1])
