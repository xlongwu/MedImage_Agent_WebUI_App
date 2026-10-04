from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.backend.app.tools.report_exporter import (
    export_rsfmri_report_package,
    get_latest_rsfmri_report_export,
    list_rsfmri_report_exports,
)
from src.backend.app.tools.report_package_validator import validate_rsfmri_report_package


def test_report_exporter_creates_manifest_and_zip(tmp_path: Path):
    d = tmp_path / "derivatives"
    rpt = tmp_path / "reports"
    w = tmp_path / "work"
    e = tmp_path / "exports"
    sub = "sub-001"
    gd = rpt / "rsfmri" / "group_summary"
    gd.mkdir(parents=True)
    (gd / "dataset_summary.json").write_text(
        json.dumps(
            {
                "ok": True,
                "subjects_total": 1,
                "subjects_with_any_qc": 1,
                "warnings_total": 0,
                "errors_total": 0,
                "stage_status_counts": {
                    "motion": {"PASS": 1, "WARNING": 0, "FAIL": 0, "MISSING": 0}
                },
            }
        ),
        encoding="utf-8",
    )
    (gd / "dataset_summary_report.md").write_text("# Summary\n", encoding="utf-8")
    (gd / "subject_metrics_table.csv").write_text(
        "subject_id,motion_status\nsub-001,PASS\n", encoding="utf-8"
    )
    qd = d / "rsfmri_qc" / sub
    qd.mkdir(parents=True)
    (qd / "motion_qc.json").write_text(
        json.dumps({"ok": True, "subject_id": sub, "motion_qc_status": "PASS"}), encoding="utf-8"
    )
    md = d / "rsfmri_metrics" / sub
    md.mkdir(parents=True)
    (md / "alff_falff_result.json").write_text(
        json.dumps({"ok": True, "subject_id": sub}), encoding="utf-8"
    )
    cd = w / "gpu" / "contracts"
    cd.mkdir(parents=True)
    (cd / "gpu_contract.json").write_text(
        json.dumps(
            {
                "ok": True,
                "backend_id": "gpu_test",
                "status": "CONTRACT_ONLY",
                "execution_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    result = export_rsfmri_report_package(
        project_dir=str(tmp_path),
        derivatives_dir=str(d),
        reports_dir=str(rpt),
        work_dir=str(w),
        exports_dir=str(e),
        export_id="test_export",
    )
    assert result["ok"] is True
    pkg = e / "rsfmri_report_package" / "test_export"
    zp = e / "rsfmri_report_package" / "test_export.zip"
    assert (pkg / "MANIFEST.json").exists()
    assert (pkg / "README.md").exists()
    assert (pkg / "index.md").exists()
    assert (pkg / "checksums" / "SHA256SUMS.txt").exists()
    assert zp.exists()
    readme = (pkg / "README.md").read_text(encoding="utf-8")
    assert "synthetic" not in readme.lower()
    assert "reviewed derivative outputs" in readme
    manifest = json.loads((pkg / "MANIFEST.json").read_text(encoding="utf-8"))
    assert all("\\" not in item["relative_path"] for item in manifest["files"])
    with zipfile.ZipFile(zp) as zf:
        assert "MANIFEST.json" in zf.namelist()
    validation = validate_rsfmri_report_package(exports_dir=str(e), export_id="test_export")
    assert validation["ok"] is True
    assert validation["stats"]["checksum_mismatch_total"] == 0
    assert validation["stats"]["missing_files_total"] == 0
    latest = get_latest_rsfmri_report_export(exports_dir=str(e))
    assert latest["ok"] is True
    assert latest["export_id"] == "test_export"
    assert latest["zip_size_bytes"] == zp.stat().st_size
    assert latest["export_summary"]["zip_size_bytes"] == zp.stat().st_size
    listing = list_rsfmri_report_exports(exports_dir=str(e))
    assert listing["ok"] is True
    assert listing["exports_total"] == 1
    assert listing["exports"][0]["zip_size_bytes"] == zp.stat().st_size


def test_report_exporter_writes_metadata_only_group_summary_when_missing(tmp_path: Path):
    d = tmp_path / "derivatives"
    rpt = tmp_path / "reports"
    w = tmp_path / "work"
    e = tmp_path / "exports"
    sub = "sub-001"
    qd = d / "rsfmri_qc" / sub
    qd.mkdir(parents=True)
    (qd / "motion_qc.json").write_text(
        json.dumps({"ok": True, "subject_id": sub, "motion_qc_status": "PASS"}), encoding="utf-8"
    )

    result = export_rsfmri_report_package(
        project_dir=str(tmp_path),
        derivatives_dir=str(d),
        reports_dir=str(rpt),
        work_dir=str(w),
        exports_dir=str(e),
        export_id="missing_group_summary",
    )

    assert result["ok"] is True
    assert any("metadata-only fallback" in item for item in result["warnings"])
    pkg = e / "rsfmri_report_package" / "missing_group_summary"
    summary = json.loads(
        (pkg / "summary" / "group_summary" / "dataset_summary.json").read_text(encoding="utf-8")
    )
    assert summary["ok"] is False
    assert summary["capability_level"] == "metadata_only"
    assert (pkg / "tables" / "subject_metrics_table.csv").exists()

    validation = validate_rsfmri_report_package(
        exports_dir=str(e), export_id="missing_group_summary"
    )
    assert validation["ok"] is True
    assert validation["stats"]["checksum_mismatch_total"] == 0
    assert validation["stats"]["missing_files_total"] == 0


def test_report_exporter_uses_native_preproc_group_summary_bridge(tmp_path: Path):
    d = tmp_path / "derivatives"
    rpt = tmp_path / "reports"
    w = tmp_path / "work"
    e = tmp_path / "exports"
    run = (
        tmp_path / "outputs" / "projects" / "demo" / "preprocessing_native_runs" / "run-native-001"
    )
    group_dir = run / "artifacts" / "group_summary"
    group_dir.mkdir(parents=True)
    validation_dir = run / "artifacts" / "validation_report"
    validation_dir.mkdir(parents=True)
    final_dir = run / "artifacts" / "final_report"
    final_dir.mkdir(parents=True)
    (group_dir / "native_group_summary.json").write_text(
        json.dumps(
            {
                "_schema_version": 1,
                "data": {
                    "summary_type": "native_preproc_group_summary",
                    "subject_count": 1,
                    "completed_subject_count": 1,
                    "blocked_subject_count": 0,
                    "subject_summaries": [{"subject_id": "sub-001", "status": "succeeded"}],
                    "limitations": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (validation_dir / "native_preproc_validation_report.json").write_text(
        json.dumps(
            {
                "_schema_version": 1,
                "data": {
                    "overall_status": "pass",
                    "summary": {
                        "artifact_failed_count": 0,
                        "truthfulness_failed_count": 0,
                        "safety_failed_count": 0,
                    },
                    "artifact_validation": {"artifacts": [], "failed_count": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    (final_dir / "native_preproc_final_report.json").write_text(
        json.dumps(
            {
                "_schema_version": 1,
                "data": {
                    "stage_results": [
                        {
                            "stage_id": "functional_connectivity",
                            "status": "succeeded",
                            "warnings": [],
                            "errors": [],
                        }
                    ],
                    "validation_summary": {
                        "summary": {
                            "artifact_failed_count": 0,
                            "truthfulness_failed_count": 0,
                            "safety_failed_count": 0,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = export_rsfmri_report_package(
        project_dir=str(tmp_path),
        derivatives_dir=str(d),
        reports_dir=str(rpt),
        work_dir=str(w),
        exports_dir=str(e),
        export_id="native_bridge_export",
    )

    assert result["ok"] is True
    assert result["exported_subjects_total"] == 1
    assert result["source_files_total"] >= 3
    assert not any("metadata-only fallback" in item for item in result["warnings"])
    pkg = e / "rsfmri_report_package" / "native_bridge_export"
    summary = json.loads(
        (pkg / "summary" / "group_summary" / "dataset_summary.json").read_text(encoding="utf-8")
    )
    assert summary["summary_type"] == "native_preproc_group_summary_bridge"
    assert summary["subjects_total"] == 1
    manifest = json.loads((pkg / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["source_roots"]["native_preproc_run"].endswith("run-native-001")
    validation = validate_rsfmri_report_package(
        exports_dir=str(e), export_id="native_bridge_export"
    )
    assert validation["ok"] is True


def test_report_exporter_collects_artifacts_from_batch_subject_validations(tmp_path: Path):
    d = tmp_path / "derivatives"
    rpt = tmp_path / "reports"
    w = tmp_path / "work"
    e = tmp_path / "exports"
    run = tmp_path / "outputs" / "projects" / "demo" / "preprocessing_native_runs" / "run-batch-001"
    group_dir = run / "artifacts" / "group_summary"
    group_dir.mkdir(parents=True)
    validation_dir = run / "artifacts" / "validation_report"
    validation_dir.mkdir(parents=True)
    final_dir = run / "artifacts" / "final_report"
    final_dir.mkdir(parents=True)
    subjects = ["sub-001", "sub-002"]
    (group_dir / "native_group_summary.json").write_text(
        json.dumps(
            {
                "summary_type": "native_preproc_group_summary",
                "subject_count": len(subjects),
                "completed_subject_count": len(subjects),
                "subject_summaries": [
                    {"subject_id": sid, "status": "succeeded"} for sid in subjects
                ],
                "limitations": [],
            }
        ),
        encoding="utf-8",
    )
    (validation_dir / "native_preproc_validation_report.json").write_text(
        json.dumps({"status": "succeeded", "subject_count": len(subjects)}),
        encoding="utf-8",
    )
    (final_dir / "native_preproc_final_report.json").write_text(
        json.dumps({"status": "succeeded", "subject_count": len(subjects)}),
        encoding="utf-8",
    )

    for sid in subjects:
        (run / sid).mkdir(parents=True, exist_ok=True)
        (run / sid / "native_full_run_manifest.json").write_text(
            json.dumps(
                {
                    "status": "succeeded",
                    "stage_results": [
                        {
                            "stage_id": "slice_timing",
                            "status": "succeeded",
                            "warnings": [],
                            "errors": [],
                        },
                        {
                            "stage_id": "realignment",
                            "status": "simplified",
                            "warnings": ["translation_only"],
                            "errors": [],
                        },
                        {
                            "stage_id": "dicom_to_nifti",
                            "status": "skipped",
                            "warnings": [],
                            "errors": [],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        artifact_dir = run / sid / "artifacts"
        motion = artifact_dir / "realignment" / "motion.tsv"
        roi = artifact_dir / "roi_timeseries" / "roi.tsv"
        fc = artifact_dir / "functional_connectivity" / "fc.npy"
        for path, content in ((motion, b"x\ty\n"), (roi, b"roi-1\n0.0\n"), (fc, b"NUMPY")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        child_validation = (
            artifact_dir / "validation_report" / "native_preproc_validation_report.json"
        )
        child_validation.parent.mkdir(parents=True, exist_ok=True)
        child_validation.write_text(
            json.dumps(
                {
                    "artifact_validation": {
                        "artifacts": [
                            {"artifact_type": "motion_parameters", "path": str(motion)},
                            {"artifact_type": "roi_timeseries", "path": str(roi)},
                            {"artifact_type": "fc_matrix", "path": str(fc)},
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

    result = export_rsfmri_report_package(
        project_dir=str(tmp_path),
        derivatives_dir=str(d),
        reports_dir=str(rpt),
        work_dir=str(w),
        exports_dir=str(e),
        export_id="native_batch_export",
    )

    assert result["exported_subjects"] == subjects
    pkg = e / "rsfmri_report_package" / "native_batch_export"
    for sid in subjects:
        assert (pkg / "subjects" / sid / "confounds" / "motion.tsv").is_file()
        assert (pkg / "fc" / sid / "roi.tsv").is_file()
        assert (pkg / "fc" / sid / "fc.npy").is_file()
    index = (pkg / "index.md").read_text(encoding="utf-8")
    assert "| slice_timing | 2 | 0 | 0 | 0 |" in index
    assert "| realignment | 0 | 2 | 0 | 0 |" in index
    assert "| dicom_to_nifti | 0 | 0 | 0 | 2 |" in index
    summary = json.loads(
        (pkg / "summary" / "group_summary" / "dataset_summary.json").read_text(encoding="utf-8")
    )
    assert summary["warnings_total"] == 2
    validation = validate_rsfmri_report_package(exports_dir=str(e), export_id="native_batch_export")
    assert validation["ok"] is True
    assert validation["stats"]["checksum_mismatch_total"] == 0


def test_report_export_listing_tolerates_incomplete_stale_export(tmp_path: Path):
    d = tmp_path / "derivatives"
    rpt = tmp_path / "reports"
    w = tmp_path / "work"
    e = tmp_path / "exports"
    sub = "sub-001"
    qd = d / "rsfmri_qc" / sub
    qd.mkdir(parents=True)
    (qd / "motion_qc.json").write_text(
        json.dumps({"ok": True, "subject_id": sub}), encoding="utf-8"
    )
    export_rsfmri_report_package(
        project_dir=str(tmp_path),
        derivatives_dir=str(d),
        reports_dir=str(rpt),
        work_dir=str(w),
        exports_dir=str(e),
        export_id="rsfmri_export_20260706_220533",
    )
    stale = e / "rsfmri_report_package" / "rsfmri_export_20990101_000000"
    stale.mkdir(parents=True)
    (stale / "checksums").mkdir()
    (stale / "checksums" / "SHA256SUMS.txt").write_text("", encoding="utf-8")

    listing = list_rsfmri_report_exports(exports_dir=str(e))

    assert listing["ok"] is True
    assert listing["exports_total"] == 2
    stale_entry = next(item for item in listing["exports"] if item["export_id"] == stale.name)
    assert stale_entry["ok"] is False
    assert stale_entry["missing_evidence"] == [
        "export_summary.json",
        "MANIFEST.json",
        "rsfmri_export_20990101_000000.zip",
    ]
    latest = get_latest_rsfmri_report_export(exports_dir=str(e))
    assert latest["ok"] is True
    assert latest["export_id"] == "rsfmri_export_20260706_220533"
